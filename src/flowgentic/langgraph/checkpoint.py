"""A LangGraph checkpointer that keeps each agent's state in a key-value store.

``KeyValueSaver(store)`` works over any ``flowgentic.store.KeyValueStore``:
RaDEX on Dragon's distributed dictionary in an HPC run, a dictionary in-process
in tests. Keys, for thread ``t`` and namespace ``ns``::

    lg/{t}/{ns}/head               the newest checkpoint: its id, the typed
                                   checkpoint, typed metadata, parent id
    lg/{t}/{ns}/w                  pending writes, with the id of the checkpoint
                                   they belong to: the newest one that has any
    lg/{t}/{ns}/c/{checkpoint_id}  an older retained checkpoint, with its writes
    lg/{t}/{ns}/index              the older retained ids, oldest first
    lg/{t}/namespaces              the thread's namespaces other than the root

Each store call is a round trip that blocks the event loop, and a RaDEX client
grows with each, so the layout keeps them few. With the default ``keep_last=1``
a checkpoint is one put, which replaces the previous one; its pending writes
are one more; reading the newest checkpoint back is two gets; nothing is
deleted. Writes under ``w`` that belong to an older checkpoint are ignored, and
replaced by the next writes. An agent that suspends while its task runs, with
``exit`` durability, costs four calls per suspension.

``put`` keeps only the newest ``keep_last`` checkpoints of a namespace: each
checkpoint holds the agent's whole history, so keeping all of them grows with
the square of a run's length. With ``keep_last`` above one, each checkpoint
also moves the previous newest one, with its writes, under ``c/``.

The store has no atomic update, so ``put_writes`` reads a key, changes it and
writes it back. That is safe because each agent's steps run in one process and
every call here is synchronous, on the event loop, so a read-modify-write never
yields to another coroutine halfway. The asynchronous methods call the
synchronous ones for that reason. The saver remembers the newest checkpoint it
put for each thread (the most recent threads only), so the first writes on a new
checkpoint need no read; anything it does not remember, it reads.

``list`` needs a thread id: RaDEX cannot list its keys.
"""

from __future__ import annotations

import random
from collections import OrderedDict
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
	WRITES_IDX_MAP,
	BaseCheckpointSaver,
	ChannelVersions,
	Checkpoint,
	CheckpointMetadata,
	CheckpointTuple,
	get_checkpoint_id,
	get_checkpoint_metadata,
	writes_sort_key,
)

from flowgentic.store import KeyValueStore

__all__ = ["KeyValueSaver"]

_REMEMBERED = 20_000  # threads whose newest checkpoint the saver remembers


class KeyValueSaver(BaseCheckpointSaver[str]):
	"""Checkpoints in a ``KeyValueStore``; see the module's docstring."""

	def __init__(
		self, store: KeyValueStore, *, keep_last: int = 1, serde: Any = None
	) -> None:
		super().__init__(serde=serde)
		if keep_last < 1:
			raise ValueError("keep_last must be at least 1")
		self.store = store
		self.keep_last = keep_last
		self.checkpoints_put = 0
		self.writes_put = 0
		self.checkpoints_read = 0
		self.checkpoint_bytes = 0
		# (thread, namespace) -> [newest checkpoint id, whether it has writes]
		self._newest: OrderedDict[tuple[str, str], list] = OrderedDict()

	@staticmethod
	def _prefix(thread_id: str, ns: str) -> str:
		return f"lg/{thread_id}/{ns}"

	@staticmethod
	def _config(thread_id: str, ns: str, checkpoint_id: str) -> RunnableConfig:
		return {
			"configurable": {
				"thread_id": thread_id,
				"checkpoint_ns": ns,
				"checkpoint_id": checkpoint_id,
			}
		}

	def _remember(
		self, thread_id: str, ns: str, checkpoint_id: str, writes: bool
	) -> None:
		key = (thread_id, ns)
		self._newest[key] = [checkpoint_id, writes]
		self._newest.move_to_end(key)
		while len(self._newest) > _REMEMBERED:
			self._newest.popitem(last=False)

	def _writes_of(self, prefix: str, checkpoint_id: str) -> list:
		"""The stored pending writes of the newest checkpoint ``checkpoint_id``."""
		saved = self.store.get(f"{prefix}/w", None)
		return list(saved[1]) if saved is not None and saved[0] == checkpoint_id else []

	def _tuple(
		self,
		thread_id: str,
		ns: str,
		checkpoint_id: str,
		saved: tuple,
		writes: list,
		config: RunnableConfig | None = None,
	) -> CheckpointTuple:
		self.checkpoints_read += 1
		checkpoint, metadata, parent_id = saved
		writes = sorted(writes, key=lambda w: writes_sort_key(w[3], w[0], w[4]))
		return CheckpointTuple(
			config=config or self._config(thread_id, ns, checkpoint_id),
			checkpoint=self.serde.loads_typed(checkpoint),
			metadata=self.serde.loads_typed(metadata),
			parent_config=self._config(thread_id, ns, parent_id) if parent_id else None,
			pending_writes=[
				(task_id, channel, self.serde.loads_typed(value))
				for task_id, channel, value, _, _ in writes
			],
		)

	def _newest_tuple(
		self, thread_id: str, ns: str, config: RunnableConfig | None = None
	) -> CheckpointTuple | None:
		prefix = self._prefix(thread_id, ns)
		head = self.store.get(f"{prefix}/head", None)
		if head is None:
			return None
		checkpoint_id = head[0]
		writes = self._writes_of(prefix, checkpoint_id)
		return self._tuple(thread_id, ns, checkpoint_id, head[1:], writes, config)

	def _older_tuple(
		self,
		thread_id: str,
		ns: str,
		checkpoint_id: str,
		config: RunnableConfig | None = None,
	) -> CheckpointTuple | None:
		saved = self.store.get(f"{self._prefix(thread_id, ns)}/c/{checkpoint_id}", None)
		if saved is None:
			return None
		return self._tuple(thread_id, ns, checkpoint_id, saved[:3], saved[3], config)

	def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
		thread_id = config["configurable"]["thread_id"]
		ns = config["configurable"].get("checkpoint_ns", "")
		checkpoint_id = get_checkpoint_id(config)
		if not checkpoint_id:
			return self._newest_tuple(thread_id, ns)
		item = self._newest_tuple(thread_id, ns, config)
		if item is not None and item.checkpoint["id"] == checkpoint_id:
			return item
		if self.keep_last == 1:
			return None
		return self._older_tuple(thread_id, ns, checkpoint_id, config)

	def _namespaces(self, thread_id: str) -> list[str]:
		return ["", *sorted(self.store.get(f"lg/{thread_id}/namespaces", set()))]

	def list(
		self,
		config: RunnableConfig | None,
		*,
		filter: dict[str, Any] | None = None,
		before: RunnableConfig | None = None,
		limit: int | None = None,
	) -> Iterator[CheckpointTuple]:
		"""The thread's retained checkpoints, newest first."""
		if not config or "thread_id" not in config.get("configurable", {}):
			raise NotImplementedError(
				"KeyValueSaver.list needs a thread_id: the store cannot list keys"
			)
		thread_id = config["configurable"]["thread_id"]
		ns_wanted = config["configurable"].get("checkpoint_ns")
		id_wanted = get_checkpoint_id(config)
		before_id = get_checkpoint_id(before) if before else None
		namespaces = (
			[ns_wanted] if ns_wanted is not None else self._namespaces(thread_id)
		)
		for ns in namespaces:
			newest = self._newest_tuple(thread_id, ns)
			if newest is None:
				continue
			older = self.store.get(f"{self._prefix(thread_id, ns)}/index", [])
			ids = [newest.checkpoint["id"], *reversed(older)]
			for checkpoint_id in ids:
				if id_wanted and checkpoint_id != id_wanted:
					continue
				if before_id and checkpoint_id >= before_id:
					continue
				item = (
					newest
					if checkpoint_id == newest.checkpoint["id"]
					else self._older_tuple(thread_id, ns, checkpoint_id)
				)
				if item is None:
					continue
				if filter and not all(
					item.metadata.get(k) == v for k, v in filter.items()
				):
					continue
				if limit is not None:
					if limit <= 0:
						return
					limit -= 1
				yield item

	def _keep_previous(self, prefix: str, checkpoint_id: str) -> None:
		"""Move the newest checkpoint, with its writes, among the older retained
		ones, and drop the oldest beyond ``keep_last``."""
		head = self.store.get(f"{prefix}/head", None)
		if head is None or head[0] == checkpoint_id:
			return
		previous = head[0]
		writes = self._writes_of(prefix, previous)
		self.store.put(f"{prefix}/c/{previous}", (*head[1:], writes))
		index = self.store.get(f"{prefix}/index", [])
		if previous not in index:
			index.append(previous)
		index.sort()
		keep = self.keep_last - 1
		dropped, index = index[:-keep], index[-keep:]
		for old in dropped:
			self.store.delete(f"{prefix}/c/{old}")
		self.store.put(f"{prefix}/index", index)

	def put(
		self,
		config: RunnableConfig,
		checkpoint: Checkpoint,
		metadata: CheckpointMetadata,
		new_versions: ChannelVersions,
	) -> RunnableConfig:
		thread_id = config["configurable"]["thread_id"]
		ns = config["configurable"].get("checkpoint_ns", "")
		prefix = self._prefix(thread_id, ns)
		checkpoint_id = checkpoint["id"]
		typed_checkpoint = self.serde.dumps_typed(checkpoint)
		typed_metadata = self.serde.dumps_typed(
			get_checkpoint_metadata(config, metadata)
		)
		self.checkpoint_bytes += len(typed_checkpoint[1]) + len(typed_metadata[1])
		parent_id = config["configurable"].get("checkpoint_id")
		if self.keep_last > 1:
			self._keep_previous(prefix, checkpoint_id)
		if ns and (thread_id, ns) not in self._newest:
			namespaces = self.store.get(f"lg/{thread_id}/namespaces", set())
			if ns not in namespaces:
				namespaces.add(ns)
				self.store.put(f"lg/{thread_id}/namespaces", namespaces)
		self.store.put(
			f"{prefix}/head",
			(checkpoint_id, typed_checkpoint, typed_metadata, parent_id),
		)
		self._remember(thread_id, ns, checkpoint_id, writes=False)
		self.checkpoints_put += 1
		return self._config(thread_id, ns, checkpoint_id)

	def put_writes(
		self,
		config: RunnableConfig,
		writes: Sequence[tuple[str, Any]],
		task_id: str,
		task_path: str = "",
	) -> None:
		thread_id = config["configurable"]["thread_id"]
		ns = config["configurable"].get("checkpoint_ns", "")
		checkpoint_id = config["configurable"]["checkpoint_id"]
		prefix = self._prefix(thread_id, ns)
		known = self._newest.get((thread_id, ns))
		older: tuple | None = None
		if known is not None and known[0] == checkpoint_id:
			# the newest checkpoint, put by this saver: no writes yet, or some to read
			stored = self._writes_of(prefix, checkpoint_id) if known[1] else []
		else:
			newest = known[0] if known is not None else None
			if newest is None and self.keep_last > 1:
				head = self.store.get(f"{prefix}/head", None)
				newest = head[0] if head is not None else None
			if newest is not None and newest != checkpoint_id:
				# an older checkpoint: its writes are kept with it, if it is kept
				if self.keep_last > 1:
					older = self.store.get(f"{prefix}/c/{checkpoint_id}", None)
				if older is None:
					return
				stored = list(older[3])
			else:
				stored = self._writes_of(prefix, checkpoint_id)
		have = {(w[0], w[4]): i for i, w in enumerate(stored)}
		changed = False
		for idx, (channel, value) in enumerate(writes):
			inner = (task_id, WRITES_IDX_MAP.get(channel, idx))
			if inner[1] >= 0 and inner in have:  # a regular write is kept once
				continue
			entry = (
				task_id,
				channel,
				self.serde.dumps_typed(value),
				task_path,
				inner[1],
			)
			if inner in have:  # a special write replaces the earlier one
				stored[have[inner]] = entry
			else:
				have[inner] = len(stored)
				stored.append(entry)
			changed = True
		if not changed:
			return
		if older is not None:
			self.store.put(f"{prefix}/c/{checkpoint_id}", (*older[:3], stored))
		else:
			self.store.put(f"{prefix}/w", (checkpoint_id, stored))
			if known is not None and known[0] == checkpoint_id:
				known[1] = True
		self.writes_put += 1

	def delete_thread(self, thread_id: str) -> None:
		for ns in self._namespaces(thread_id):
			prefix = self._prefix(thread_id, ns)
			for checkpoint_id in self.store.get(f"{prefix}/index", []):
				self.store.delete(f"{prefix}/c/{checkpoint_id}")
			for key in ("head", "w", "index"):
				self.store.delete(f"{prefix}/{key}")
			self._newest.pop((thread_id, ns), None)
		self.store.delete(f"lg/{thread_id}/namespaces")

	def get_next_version(self, current: str | None, channel: None) -> str:
		"""As ``InMemorySaver.get_next_version`` in langgraph-checkpoint 4.3."""
		if current is None:
			current_v = 0
		elif isinstance(current, int):
			current_v = current
		else:
			current_v = int(current.split(".")[0])
		# not for security: a tie-breaker, as in LangGraph's own savers
		return f"{current_v + 1:032}.{random.random():016}"  # noqa: S311  # nosec B311

	async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
		return self.get_tuple(config)

	async def alist(
		self,
		config: RunnableConfig | None,
		*,
		filter: dict[str, Any] | None = None,
		before: RunnableConfig | None = None,
		limit: int | None = None,
	) -> AsyncIterator[CheckpointTuple]:
		for item in list(self.list(config, filter=filter, before=before, limit=limit)):
			yield item

	async def aput(
		self,
		config: RunnableConfig,
		checkpoint: Checkpoint,
		metadata: CheckpointMetadata,
		new_versions: ChannelVersions,
	) -> RunnableConfig:
		return self.put(config, checkpoint, metadata, new_versions)

	async def aput_writes(
		self,
		config: RunnableConfig,
		writes: Sequence[tuple[str, Any]],
		task_id: str,
		task_path: str = "",
	) -> None:
		return self.put_writes(config, writes, task_id, task_path)

	async def adelete_thread(self, thread_id: str) -> None:
		return self.delete_thread(thread_id)

	def stats(self) -> dict:
		return {
			"checkpoints_put": self.checkpoints_put,
			"writes_put": self.writes_put,
			"checkpoints_read": self.checkpoints_read,
			"checkpoint_bytes_mean": (
				round(self.checkpoint_bytes / self.checkpoints_put)
				if self.checkpoints_put
				else None
			),
		}
