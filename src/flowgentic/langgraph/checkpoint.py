"""A LangGraph checkpointer that keeps each agent's state in a key-value store.

``KeyValueSaver(store)`` works over any ``flowgentic.store.KeyValueStore``:
RaDEX on Dragon's distributed dictionary in an HPC run, a dictionary in-process
in tests. Keys, for thread ``t`` and namespace ``ns``::

    lg/{t}/{ns}/head               the newest checkpoint: its id, the typed
                                   checkpoint, typed metadata, parent id
    lg/{t}/{ns}/w                  pending writes grouped by checkpoint id
    lg/{t}/{ns}/c/{checkpoint_id}  an older retained checkpoint, with its writes
    lg/{t}/{ns}/index              the older retained ids, oldest first
    lg/{t}/namespaces              the thread's namespaces other than the root

Each store call is a round trip that blocks the event loop, and a RaDEX client
grows with each, so the layout keeps them few. With the default ``keep_last=1``
a checkpoint is one put, which replaces the previous one; its pending writes
take two reads and one put; reading the newest checkpoint back is two gets; nothing is
deleted. Pending writes are grouped by checkpoint id. Older batches are pruned
on the next write to the head. An agent that suspends while its task runs, with
``exit`` durability, costs six calls per suspension in the root namespace with
ordinary channels. Delta channels also read their parent to retain its replay data.

``put`` keeps only the newest ``keep_last`` checkpoints of a namespace: each
checkpoint holds the agent's whole history, so keeping all of them grows with
the square of a run's length. With ``keep_last`` above one, each checkpoint
also moves the previous newest one, with its writes, under ``c/``.

One process owns each thread's writes. Within a process, the saver serializes
updates for a thread and reads the persisted head and pending writes before
merging a batch. Reopening a store or retrying a save preserves earlier writes.

``list`` needs a thread id: RaDEX cannot list its keys.
"""

from __future__ import annotations

import heapq
import os
import random
import threading
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

# A synchronous LangGraph run may save on several executor threads. Savers
# attached to the same store in this process must serialize a thread's updates.
_locks = tuple(threading.RLock() for _ in range(64))


def _reset_locks_after_fork() -> None:
	global _locks
	_locks = tuple(threading.RLock() for _ in range(64))


if hasattr(os, "register_at_fork"):
	os.register_at_fork(after_in_child=_reset_locks_after_fork)


def _thread_lock(thread_id: str):
	return _locks[hash(thread_id) % len(_locks)]


def _key_part(value: str) -> str:
	return str(value).replace("%", "%25").replace("/", "%2F")


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
		return f"lg/{_key_part(thread_id)}/{_key_part(ns)}"

	@staticmethod
	def _config(thread_id: str, ns: str, checkpoint_id: str) -> RunnableConfig:
		return {
			"configurable": {
				"thread_id": thread_id,
				"checkpoint_ns": ns,
				"checkpoint_id": checkpoint_id,
			}
		}

	def _write_batches(self, prefix: str) -> dict[str, list]:
		saved = self.store.get(f"{prefix}/w", {})
		if isinstance(saved, tuple):  # Original single-checkpoint representation.
			return {saved[0]: saved[1]}
		return saved

	def _writes_of(self, prefix: str, checkpoint_id: str) -> list:
		return self._write_batches(prefix).get(checkpoint_id, [])

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
			checkpoint=self.serde.loads_typed(checkpoint[:2]),
			metadata=self.serde.loads_typed(metadata),
			parent_config=self._config(thread_id, ns, parent_id) if parent_id else None,
			pending_writes=[
				(task_id, channel, self.serde.loads_typed(value))
				for task_id, channel, value, _, _ in writes
			],
		)

	def _older_tuple(
		self,
		thread_id: str,
		ns: str,
		checkpoint_id: str,
		config: RunnableConfig | None = None,
	) -> CheckpointTuple | None:
		saved = self.store.get(
			f"{self._prefix(thread_id, ns)}/c/{_key_part(checkpoint_id)}", None
		)
		if saved is None:
			return None
		return self._tuple(thread_id, ns, checkpoint_id, saved[:3], saved[3], config)

	def _record(self, thread_id: str, ns: str, checkpoint_id: str | None):
		prefix = self._prefix(thread_id, ns)
		head = self.store.get(f"{prefix}/head", None)
		if head is not None and (checkpoint_id is None or head[0] == checkpoint_id):
			return head[0], head[1:], self._writes_of(prefix, head[0])
		if checkpoint_id is not None and self.keep_last > 1:
			old = self.store.get(f"{prefix}/c/{_key_part(checkpoint_id)}", None)
			if old is not None:
				return checkpoint_id, old[:3], old[3]
		return None

	def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
		thread_id = config["configurable"]["thread_id"]
		ns = config["configurable"].get("checkpoint_ns", "")
		checkpoint_id = get_checkpoint_id(config)
		with _thread_lock(thread_id):
			record = self._record(thread_id, ns, checkpoint_id)
		if record is None:
			return None
		return self._tuple(thread_id, ns, *record, config if checkpoint_id else None)

	def _history(self, typed: tuple) -> dict:
		# A checkpoint with unsnapshotted delta channels carries their inherited
		# seed and writes beside its ordinary serializer (type, bytes) pair.
		return self.serde.loads_typed(typed[2]) if len(typed) == 3 else {}

	def get_delta_channel_history(
		self, *, config: RunnableConfig, channels: Sequence[str]
	) -> dict:
		settings = config["configurable"]
		thread = settings["thread_id"]
		with _thread_lock(thread):
			record = self._record(
				thread, settings.get("checkpoint_ns", ""), get_checkpoint_id(config)
			)
			saved = self._history(record[1][0]) if record else {}
			missing = [ch for ch in channels if ch not in saved]
			history = (
				super().get_delta_channel_history(config=config, channels=missing)
				if missing
				else {}
			)
			return {ch: saved[ch] if ch in saved else history[ch] for ch in channels}

	async def aget_delta_channel_history(
		self, *, config: RunnableConfig, channels: Sequence[str]
	) -> dict:
		return self.get_delta_channel_history(config=config, channels=channels)

	def _serialize_checkpoint(
		self,
		config: RunnableConfig,
		checkpoint: Checkpoint,
		metadata: CheckpointMetadata,
	) -> tuple:
		typed = self.serde.dumps_typed(checkpoint)
		channels = (
			metadata.get("counters_since_delta_snapshot") or {}
		).keys() - checkpoint["channel_values"].keys()
		parent_id = get_checkpoint_id(config)
		if not channels or parent_id is None:
			return typed
		thread = config["configurable"]["thread_id"]
		ns = config["configurable"].get("checkpoint_ns", "")
		record = self._record(thread, ns, parent_id)
		if record is None:
			# An idempotent retry can arrive after this checkpoint replaced its parent.
			existing = self._record(thread, ns, checkpoint["id"])
			if existing is not None:
				return (*typed, *existing[1][0][2:])
			raise ValueError(f"delta checkpoint requires missing parent {parent_id!r}")
		parent = self._tuple(thread, ns, *record)
		values = parent.checkpoint["channel_values"]
		inherited = self._history(record[1][0])
		history = {}
		for ch in channels:
			entry = (
				{"seed": values[ch], "writes": []}
				if ch in values
				else inherited.get(ch, {"writes": []})
			)
			entry["writes"].extend(w for w in parent.pending_writes if w[1] == ch)
			history[ch] = entry
		return (*typed, self.serde.dumps_typed(history))

	def _register_namespace(self, thread_id: str, ns: str) -> None:
		if ns:
			namespaces = self.store.get(f"lg/{_key_part(thread_id)}/namespaces", set())
			if ns not in namespaces:
				namespaces.add(ns)
				self.store.put(f"lg/{_key_part(thread_id)}/namespaces", namespaces)

	def _namespaces(self, thread_id: str) -> list[str]:
		return [
			"",
			*sorted(self.store.get(f"lg/{_key_part(thread_id)}/namespaces", set())),
		]

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

		def retained(ns: str) -> Iterator[tuple[str, str, tuple | None]]:
			prefix = self._prefix(thread_id, ns)
			head = self.store.get(f"{prefix}/head", None)
			if head is not None:
				yield head[0], ns, head
				for cid in reversed(self.store.get(f"{prefix}/index", [])):
					yield cid, ns, None

		for checkpoint_id, ns, head in heapq.merge(
			*(retained(ns) for ns in namespaces), reverse=True
		):
			if limit is not None and limit <= 0:
				return
			if id_wanted and checkpoint_id != id_wanted:
				continue
			if before_id and checkpoint_id >= before_id:
				continue
			if head is None:
				item = self._older_tuple(thread_id, ns, checkpoint_id)
			else:
				writes = self._writes_of(self._prefix(thread_id, ns), checkpoint_id)
				item = self._tuple(thread_id, ns, checkpoint_id, head[1:], writes)
			if item is None or (
				filter and not all(item.metadata.get(k) == v for k, v in filter.items())
			):
				continue
			yield item
			if limit is not None:
				limit -= 1

	def _keep_previous(self, prefix: str, checkpoint_id: str) -> None:
		"""Move the newest checkpoint, with its writes, among the older retained
		ones, and drop the oldest beyond ``keep_last``."""
		head = self.store.get(f"{prefix}/head", None)
		if head is None or head[0] == checkpoint_id:
			return
		previous = head[0]
		writes = self._writes_of(prefix, previous)
		self.store.put(f"{prefix}/c/{_key_part(previous)}", (*head[1:], writes))
		index = self.store.get(f"{prefix}/index", [])
		if previous not in index:
			index.append(previous)
		index.sort()
		keep = self.keep_last - 1
		dropped, index = index[:-keep], index[-keep:]
		for old in dropped:
			self.store.delete(f"{prefix}/c/{_key_part(old)}")
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
		with _thread_lock(thread_id):
			prefix = self._prefix(thread_id, ns)
			checkpoint_id = checkpoint["id"]
			typed_checkpoint = self._serialize_checkpoint(config, checkpoint, metadata)
			typed_metadata = self.serde.dumps_typed(
				get_checkpoint_metadata(config, metadata)
			)
			self.checkpoint_bytes += len(typed_checkpoint[1]) + len(typed_metadata[1])
			if len(typed_checkpoint) == 3:
				self.checkpoint_bytes += len(typed_checkpoint[2][1])
			parent_id = config["configurable"].get("checkpoint_id")
			if self.keep_last > 1:
				self._keep_previous(prefix, checkpoint_id)
			self._register_namespace(thread_id, ns)
			self.store.put(
				f"{prefix}/head",
				(checkpoint_id, typed_checkpoint, typed_metadata, parent_id),
			)
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
		with _thread_lock(thread_id):
			prefix = self._prefix(thread_id, ns)
			head = self.store.get(f"{prefix}/head", None)
			if head is None:
				self._register_namespace(thread_id, ns)
			older = None
			if head is not None and checkpoint_id < head[0]:
				if self.keep_last == 1:
					return
				older = self.store.get(f"{prefix}/c/{_key_part(checkpoint_id)}", None)
				if older is None:
					return
				stored = list(older[3])
			else:
				# Background writes may arrive before their checkpoint is published.
				batches = self._write_batches(prefix)
				if head is not None:
					batches = {
						cid: batch for cid, batch in batches.items() if cid >= head[0]
					}
				stored = batches.setdefault(checkpoint_id, [])
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
				self.store.put(
					f"{prefix}/c/{_key_part(checkpoint_id)}", (*older[:3], stored)
				)
			else:
				self.store.put(f"{prefix}/w", batches)
			self.writes_put += 1

	def delete_thread(self, thread_id: str) -> None:
		with _thread_lock(thread_id):
			for ns in self._namespaces(thread_id):
				prefix = self._prefix(thread_id, ns)
				for checkpoint_id in self.store.get(f"{prefix}/index", []):
					self.store.delete(f"{prefix}/c/{_key_part(checkpoint_id)}")
				for key in ("head", "w", "index"):
					self.store.delete(f"{prefix}/{key}")
			self.store.delete(f"lg/{_key_part(thread_id)}/namespaces")

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
		for item in self.list(config, filter=filter, before=before, limit=limit):
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
		"""What this saver has put and read, and its checkpoints' mean size in bytes."""
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
