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

# Values come from processes of the same job.
import pickle  # nosec B403
import threading
from typing import Any, Protocol, runtime_checkable

__all__ = ["MISSING", "KeyValueStore", "MemoryStore", "RadexStore"]


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
		"""Store an array as float64."""
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
		except ImportError as exc:  # pragma: no cover - depends on the machine
			raise ImportError(
				"RadexStore needs RaDEX's Python client, which is built from source "
				"with its C++ core: see https://github.com/radical-cybertools/radex"
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
