"""Key-value stores in the allocation's memory: RaDEX, or a dictionary in-process.

A ``KeyValueStore`` holds picklable values under string keys. The LangGraph
checkpointer (``flowgentic.langgraph.KeyValueSaver``) keeps agents' state in
one, and tools can share tensors through the same client.

- ``RadexStore``: RaDEX's client on a Dragon distributed dictionary.
  ``RadexStore.attach(descriptor)`` returns this process's one client for that
  dictionary: every attach costs Dragon channels, so a process attaches once.
  RaDEX is built from source; it is imported only when a store is attached.
- ``MemoryStore``: a dictionary in this process, for tests and local runs. It
  pickles values as RaDEX does, so callers share no objects.

Values are pickled: a store must be trusted by every process that reads it,
which holds for a store inside one job's allocation.
"""

from __future__ import annotations

import os

# Values come from processes of the same job.
import pickle  # nosec B403
import threading
from collections import OrderedDict
from typing import Any, Protocol, runtime_checkable

__all__ = ["MISSING", "KeyValueStore", "MemoryStore", "RadexStore", "WriteThroughStore"]


class _Missing:
	def __repr__(self) -> str:
		return "MISSING"


MISSING: Any = _Missing()
"""The default of ``get``: raise ``KeyError`` for a missing key."""


@runtime_checkable
class KeyValueStore(Protocol):
	"""What the checkpointer needs from a store."""

	def get(self, key: str, default: Any = MISSING) -> Any:
		"""The value under ``key``; ``default`` if missing, or ``KeyError``."""
		...

	def put(self, key: str, value: Any) -> None:
		"""Store ``value`` under ``key``, replacing any value there."""
		...

	def delete(self, key: str) -> None:
		"""Remove ``key``. A missing key is not an error."""
		...

	def contains(self, key: str) -> bool:
		"""Whether ``key`` holds a value."""
		...


class WriteThroughStore:
	"""A bounded read cache for a store partition exclusively owned by one process.

	Route *all* access to the partition through one wrapper while it is owned.
	Share it among savers in that process. On handoff, stop the old owner and
	create a new wrapper at the receiving owner, including when handing back.
	Every put and delete reaches the backing store before returning. Hits return
	freshly unpickled values so one reader cannot change another reader's data.
	"""

	def __init__(
		self, store: KeyValueStore, *, max_bytes: int, max_entries: int = 16_384
	) -> None:
		if max_bytes <= 0 or max_entries <= 0:
			raise ValueError("max_bytes and max_entries must be positive")
		self.store = store
		self.max_bytes = max_bytes
		self.max_entries = max_entries
		self._cache: OrderedDict[str, bytes] = OrderedDict()
		self._bytes = 0
		self._lock = threading.Lock()
		# Serialize operations on each key. Unrelated keys can do backing I/O
		# concurrently, without holding the LRU bookkeeping lock.
		self._key_locks = tuple(threading.Lock() for _ in range(64))

	def _forget(self, key: str) -> None:
		# Called with the cache lock held.
		old = self._cache.pop(key, None)
		if old is not None:
			self._bytes -= len(old)

	def _remember(self, key: str, value: Any) -> None:
		try:
			data = pickle.dumps(value, protocol=5)
		except Exception:
			# RaDEX accepts cloudpickle values that standard pickle cannot encode.
			# The backing operation succeeded and the cache is optional.
			return
		if len(data) > self.max_bytes:
			return
		with self._lock:
			self._cache[key] = data
			self._bytes += len(data)
			while self._bytes > self.max_bytes or len(self._cache) > self.max_entries:
				_, evicted = self._cache.popitem(last=False)
				self._bytes -= len(evicted)

	def get(self, key: str, default: Any = MISSING) -> Any:
		with self._key_locks[hash(key) % len(self._key_locks)]:
			with self._lock:
				data = self._cache.get(key)
				if data is not None:
					self._cache.move_to_end(key)
			if data is not None:
				return pickle.loads(data)  # nosec B301 - cached from the trusted store
			try:
				value = self.store.get(key)
			except KeyError:
				if default is MISSING:
					raise
				return default
			self._remember(key, value)
			return value

	def put(self, key: str, value: Any) -> None:
		with self._key_locks[hash(key) % len(self._key_locks)]:
			# Evict first: a backing write can commit and still raise.
			with self._lock:
				self._forget(key)
			self.store.put(key, value)
			self._remember(key, value)

	def delete(self, key: str) -> None:
		with self._key_locks[hash(key) % len(self._key_locks)]:
			with self._lock:
				self._forget(key)
			self.store.delete(key)

	def contains(self, key: str) -> bool:
		with self._key_locks[hash(key) % len(self._key_locks)]:
			with self._lock:
				if key in self._cache:
					return True
			return self.store.contains(key)


class MemoryStore:
	"""A dictionary in this process, shared by its threads."""

	def __init__(self) -> None:
		self._d: dict[str, bytes] = {}
		self._lock = threading.Lock()

	def get(self, key: str, default: Any = MISSING) -> Any:
		with self._lock:
			data = self._d.get(key)
		if data is None:
			if default is MISSING:
				raise KeyError(key)
			return default
		# Written by this store.
		return pickle.loads(data)  # nosec B301

	def put(self, key: str, value: Any) -> None:
		data = pickle.dumps(value, protocol=5)
		with self._lock:
			self._d[key] = data

	def delete(self, key: str) -> None:
		with self._lock:
			self._d.pop(key, None)

	def contains(self, key: str) -> bool:
		with self._lock:
			return key in self._d

	def put_tensor(self, key: str, array: Any) -> None:
		"""Store an array as float64. Tensors need NumPy, as RaDEX's client does."""
		import numpy as np

		self.put(key, np.array(array, dtype=np.float64))

	def get_tensor(self, key: str) -> Any:
		return self.get(key)

	def keys(self) -> list[str]:
		with self._lock:
			return list(self._d)


class RadexStore:
	"""RaDEX's Dragon client, as a ``KeyValueStore``. Use ``attach``."""

	_clients: dict[str, RadexStore] = {}
	_clients_lock = threading.Lock()

	def __init__(self, descriptor: str, timeout: int = 30) -> None:
		try:
			from radex.clients.core import DragonClient
			from radex.exceptions import KeyNotFoundError
			from radex.handles.handles import IncomingHandle, OutgoingHandle
		except ModuleNotFoundError as exc:
			# Only RaDEX itself missing; a partial build keeps its own error.
			if exc.name != "radex":
				raise
			raise ModuleNotFoundError(
				"RadexStore needs RaDEX's Python client, which is built from source "
				"with its C++ core: see https://github.com/radical-cybertools/radex",
				name=exc.name,
			) from exc
		self._client = DragonClient(descriptor, timeout)
		self._missing = KeyNotFoundError
		self._in = IncomingHandle
		self._out = OutgoingHandle

	@classmethod
	def attach(cls, descriptor: str, timeout: int = 30) -> RadexStore:
		"""This process's client for the dictionary ``descriptor`` names."""
		with cls._clients_lock:
			store = cls._clients.get(descriptor)
			if store is None:
				store = cls._clients[descriptor] = cls(descriptor, timeout)
			return store

	def get(self, key: str, default: Any = MISSING) -> Any:
		try:
			return self._client.get_picklable(key)
		except self._missing as exc:
			if default is MISSING:
				raise KeyError(key) from exc
			return default

	def put(self, key: str, value: Any) -> None:
		self._client.put_picklable(key, value)

	def delete(self, key: str) -> None:
		# Deleting a missing key blocks for the attach timeout: check first.
		if self._client.contains(key):
			self._client.delete_item(self._out(key))

	def contains(self, key: str) -> bool:
		return self._client.contains(key)

	def put_tensor(self, key: str, array: Any) -> None:
		"""Store an array as a float64 tensor."""
		import numpy as np

		self._client.put_tensor(
			self._out(key), np.ascontiguousarray(array, dtype=np.float64)
		)

	def get_tensor(self, key: str) -> Any:
		try:
			return self._client.get_tensor(self._in(key))
		except self._missing as exc:
			raise KeyError(key) from exc


def _reset_radex_clients_after_fork() -> None:
	# Dragon channels belong to the attaching process. The inherited lock
	# can also have been held by a thread that did not survive the fork.
	RadexStore._clients = {}
	RadexStore._clients_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
	os.register_at_fork(after_in_child=_reset_radex_clients_after_fork)
