"""A LangGraph checkpointer that keeps each agent's state in a key-value store.

``KeyValueSaver(store)`` works over any ``flowgentic.store.KeyValueStore``:
RaDEX on Dragon's distributed dictionary in an HPC run, a dictionary in-process
in tests. Keys, for thread ``t`` and namespace ``ns``::

    lg/{t}/{ns}/c/{checkpoint_id}  the typed checkpoint, typed metadata, parent id
    lg/{t}/{ns}/latest             the newest checkpoint id
    lg/{t}/{ns}/index              the retained checkpoint ids, oldest first
    lg/{t}/{ns}/w/{checkpoint_id}  that checkpoint's pending writes
    lg/{t}/namespaces              the namespaces the thread used

``put`` keeps only the newest ``keep_last`` checkpoints of a namespace: each
checkpoint holds the agent's whole history, so keeping all of them grows with
the square of a run's length.

With ``exit`` durability and agents that suspend while their tasks run, an
agent writes one checkpoint per suspension and one at its end.

The store has no atomic update, so ``put`` and ``put_writes`` read a key,
change it and write it back. That is safe because each agent's steps run in one
process and every call here is synchronous, on the event loop, so a
read-modify-write never yields to another coroutine halfway. The asynchronous
methods call the synchronous ones for that reason.

``list`` needs a thread id: RaDEX cannot list its keys.
"""

from __future__ import annotations

import random
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

	def _tuple(
		self,
		thread_id: str,
		ns: str,
		checkpoint_id: str,
		config: RunnableConfig | None = None,
	) -> CheckpointTuple | None:
		prefix = self._prefix(thread_id, ns)
		saved = self.store.get(f"{prefix}/c/{checkpoint_id}", None)
		if saved is None:
			return None
		self.checkpoints_read += 1
		checkpoint, metadata, parent_id = saved
		writes = self.store.get(f"{prefix}/w/{checkpoint_id}", [])
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

	def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
		thread_id = config["configurable"]["thread_id"]
		ns = config["configurable"].get("checkpoint_ns", "")
		if checkpoint_id := get_checkpoint_id(config):
			return self._tuple(thread_id, ns, checkpoint_id, config)
		latest = self.store.get(f"{self._prefix(thread_id, ns)}/latest", None)
		return None if latest is None else self._tuple(thread_id, ns, latest)

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
			[ns_wanted]
			if ns_wanted is not None
			else sorted(self.store.get(f"lg/{thread_id}/namespaces", set()))
		)
		for ns in namespaces:
			index = self.store.get(f"{self._prefix(thread_id, ns)}/index", [])
			for checkpoint_id in reversed(index):
				if id_wanted and checkpoint_id != id_wanted:
					continue
				if before_id and checkpoint_id >= before_id:
					continue
				item = self._tuple(thread_id, ns, checkpoint_id)
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
		self.store.put(
			f"{prefix}/c/{checkpoint_id}", (typed_checkpoint, typed_metadata, parent_id)
		)
		index = self.store.get(f"{prefix}/index", None)
		if index is None:  # the namespace's first checkpoint: record the namespace
			index = []
			namespaces = self.store.get(f"lg/{thread_id}/namespaces", set())
			if ns not in namespaces:
				namespaces.add(ns)
				self.store.put(f"lg/{thread_id}/namespaces", namespaces)
		if checkpoint_id not in index:
			index.append(checkpoint_id)
		index.sort()
		dropped, index = index[: -self.keep_last], index[-self.keep_last :]
		self.store.put(f"{prefix}/latest", index[-1])
		self.store.put(f"{prefix}/index", index)
		for old in dropped:
			self.store.delete(f"{prefix}/c/{old}")
			self.store.delete(f"{prefix}/w/{old}")
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
		key = f"{self._prefix(thread_id, ns)}/w/{checkpoint_id}"
		stored = self.store.get(key, [])
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
		if changed:
			self.store.put(key, stored)
			self.writes_put += 1

	def delete_thread(self, thread_id: str) -> None:
		for ns in self.store.get(f"lg/{thread_id}/namespaces", set()):
			prefix = self._prefix(thread_id, ns)
			for checkpoint_id in self.store.get(f"{prefix}/index", []):
				self.store.delete(f"{prefix}/c/{checkpoint_id}")
				self.store.delete(f"{prefix}/w/{checkpoint_id}")
			self.store.delete(f"{prefix}/latest")
			self.store.delete(f"{prefix}/index")
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
