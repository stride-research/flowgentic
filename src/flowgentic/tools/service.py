"""The tool service: one process that calls MCP tools for many agent processes.

Agent processes send requests on one request queue; the service answers each
process on its own reply queue. The queues are the caller's: anything with
``put(bytes)``, ``get(timeout=...)`` and ``get(block=False)`` that raises
``queue.Empty``, such as Dragon's queues, ``multiprocessing.Queue`` or
``queue.Queue``. Flowgentic imports nothing from Dragon.

In the service process::

    pool = SupervisedMcpPool(urls, clients=8, per_client=16)
    stats = await serve(requests, replies, pool)

In agent process ``i``::

    client = ToolServiceClient(requests, replies[i], index=i)
    client.start()  # on the agent process's event loop
    result = await client.call(uid, "lookup", {"key": "x"})

A repeated uid is answered without calling again: while the first call runs,
the new asker joins its waiters; once it has ended, the asker gets the kept
answer at once. A ``None`` on the request queue stops the service.

Requests and answers travel in batches of up to 512, pickled. Pickle is safe
here only because both ends are processes of the same job; the queues must not
be reachable from outside it.
"""

from __future__ import annotations

import asyncio
import json
import logging

# Both ends of the queues belong to one job.
import pickle  # nosec B403
import queue
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # the agent processes' side needs no MCP client
	from flowgentic.tools.mcp import SupervisedMcpPool

__all__ = ["ToolService", "ToolServiceClient", "ToolServiceError", "serve"]

log = logging.getLogger(__name__)

CHUNK = 512  # requests or answers per queue message


def _pack(obj: Any) -> bytes:
	return pickle.dumps(obj, protocol=5)


def _unpack(data: bytes) -> Any:
	# Written by a process of the same job.
	return pickle.loads(data)  # nosec B301


class ToolServiceError(Exception):
	"""The service could not complete a call; the message says why."""


def mcp_data(result: Any) -> tuple[bool, Any]:
	"""``(ok, data)`` from an MCP ``CallToolResult``: its structured content or
	its text."""
	is_error = getattr(result, "is_error", None)
	if is_error is None:
		is_error = getattr(result, "isError", False)
	texts = [getattr(b, "text", None) for b in (getattr(result, "content", None) or [])]
	texts = [t for t in texts if t]
	if is_error:
		return False, " ".join(texts)[:500] or "the tool failed"
	structured = getattr(result, "structured_content", None)
	if structured is None:
		structured = getattr(result, "structuredContent", None)
	if isinstance(structured, dict):
		if set(structured) == {"result"}:
			return True, structured["result"]
		return True, structured
	for text in texts:
		try:
			return True, json.loads(text)
		except ValueError:
			return True, {"text": text}
	return False, f"no data in MCP result {result!r}"[:300]


class _QueueReader(threading.Thread):
	"""Blocks on a queue and hands what arrived to the event loop, in batches.

	Each message is a pickled list of items, or ``None``, which stops the
	reader after ``on_stop`` runs on the loop. ``stop()`` stops it within a
	second.
	"""

	def __init__(
		self,
		q: Any,
		loop: asyncio.AbstractEventLoop,
		handler: Callable[[list, float], None],
		on_stop: Callable[[], None] | None = None,
		name: str = "flowgentic-queue-reader",
		max_messages: int = 256,
	) -> None:
		super().__init__(name=name, daemon=True)
		self.q = q
		self.loop = loop
		self.handler = handler
		self.on_stop = on_stop
		self.max_messages = max_messages
		self.passes = 0
		self._halt = threading.Event()

	def stop(self) -> None:
		self._halt.set()

	def run(self) -> None:
		while not self._halt.is_set():
			try:
				raw = self.q.get(timeout=1.0)
			except queue.Empty:
				continue
			except Exception as exc:  # a queue error must not end the reader silently
				log.warning("%s: get failed: %r", self.name, exc)
				time.sleep(0.1)
				continue
			raws = [raw]
			while len(raws) < self.max_messages:
				try:
					raws.append(self.q.get(block=False))
				except Exception:  # queue.Empty, or a runtime's own equivalent
					break
			items: list = []
			stop = False
			for r in raws:
				message = _unpack(r)
				if message is None:
					stop = True
				else:
					items.extend(message)
			self.passes += 1
			if items:
				self.loop.call_soon_threadsafe(self.handler, items, time.time())
			if stop:
				if self.on_stop is not None:
					self.loop.call_soon_threadsafe(self.on_stop)
				return


class _Call:
	__slots__ = ("answer", "t_recv", "waiters")

	def __init__(self, src: int, t_recv: float) -> None:
		self.waiters = [src]
		self.answer: tuple | None = None
		self.t_recv = t_recv


class ToolService:
	"""What ``serve`` runs: MCP calls through ``pool``, answered through
	``out(src, answers)``. ``serve`` is the way to run it; a benchmark of the
	service can drive it directly, handing ``on_requests`` a burst.
	"""

	def __init__(
		self,
		pool: SupervisedMcpPool,
		out: Callable[[int, list], None],
		call_timeout: float = 120.0,
		listener: Callable[[str, str, float, float, float, bool], None] | None = None,
	) -> None:
		self.pool = pool
		self.out = out
		self.call_timeout = call_timeout
		self.listener = listener
		self.calls: dict[str, _Call] = {}
		self.outbox: dict[int, list] = defaultdict(list)
		self._flush_scheduled = False
		self.inflight = 0
		self.received = 0
		self.repeats_running = 0
		self.repeats_done = 0
		self.ok: Counter[str] = Counter()
		self.failed: Counter[str] = Counter()
		self.failures: list[str] = []
		self._tasks: set[asyncio.Task] = set()

	def on_requests(self, requests: list, t_recv: float) -> None:
		loop = asyncio.get_running_loop()
		for uid, tool, args, src, _t_ask in requests:
			self.received += 1
			call = self.calls.get(uid)
			if call is None:
				call = self.calls[uid] = _Call(src, t_recv)
				self.inflight += 1
				task = loop.create_task(self._call(uid, call, tool, args))
				self._tasks.add(task)
				task.add_done_callback(self._tasks.discard)
			elif call.answer is None:
				self.repeats_running += 1
				call.waiters.append(src)
			else:
				# answered at once: its own times, not the first call's
				self.repeats_done += 1
				uid_, ok, payload, _ = call.answer
				self._queue(src, (uid_, ok, payload, (t_recv, t_recv, t_recv)))

	async def _call(self, uid: str, call: _Call, tool: str, args: dict) -> None:
		t_call = time.time()
		try:
			result = await self.pool.call(
				tool, args, timeout=self.call_timeout, idempotent=True
			)
			ok, payload = mcp_data(result)
		except Exception as exc:  # reported to the agent
			ok, payload = False, f"{type(exc).__name__}: {exc}"[:500]
		t_done = time.time()
		self.inflight -= 1
		(self.ok if ok else self.failed)[tool] += 1
		if not ok and len(self.failures) < 10:
			self.failures.append(f"{uid}: {payload}"[:300])
		if self.listener is not None:
			self.listener(uid, tool, call.t_recv, t_call, t_done, ok)
		call.answer = (uid, ok, payload, (call.t_recv, t_call, t_done))
		for src in call.waiters:
			self._queue(src, call.answer)
		call.waiters = []

	def _queue(self, src: int, answer: tuple) -> None:
		self.outbox[src].append(answer)
		if not self._flush_scheduled:
			self._flush_scheduled = True
			asyncio.get_running_loop().call_soon(self.flush)

	def flush(self) -> None:
		self._flush_scheduled = False
		outbox, self.outbox = self.outbox, defaultdict(list)
		for src, items in outbox.items():
			self.out(src, items)

	async def drain(self, timeout: float) -> None:
		end = time.monotonic() + timeout
		while self.inflight and time.monotonic() < end:
			await asyncio.sleep(0.05)
		self.flush()

	def stats(self) -> dict:
		return {
			"received": self.received,
			"unique": len(self.calls),
			"repeats_running": self.repeats_running,
			"repeats_done": self.repeats_done,
			"ok": dict(self.ok),
			"failed": dict(self.failed),
			"failures": self.failures,
		}


async def serve(
	requests: Any,
	replies: list,
	pool: SupervisedMcpPool,
	*,
	call_timeout: float = 120.0,
	connect_timeout: float = 60.0,
	on_ready: Callable[[dict], Awaitable[None] | None] | None = None,
	listener: Callable[[str, str, float, float, float, bool], None] | None = None,
	drain_timeout: float = 60.0,
) -> dict:
	"""Serve MCP calls until ``None`` arrives on ``requests``; return counters.

	``replies[i]`` is agent process ``i``'s reply queue. The pool is started
	here, with a client up on every server, and closed at the end. ``on_ready``
	is called with the pool's state once it is serving. ``listener(uid, tool,
	received, called, answered, ok)`` is called once per call, for whoever keeps
	per-call times.
	"""
	loop = asyncio.get_running_loop()
	messages = [0]

	def out(src: int, items: list) -> None:
		for i in range(0, len(items), CHUNK):
			replies[src].put(_pack(items[i : i + CHUNK]))
			messages[0] += 1

	await pool.start(wait=len(pool.servers), timeout=connect_timeout)
	service = ToolService(pool, out, call_timeout, listener)
	stopped = asyncio.Event()
	reader = _QueueReader(
		requests,
		loop,
		service.on_requests,
		on_stop=stopped.set,
		name="flowgentic-requests",
	)
	reader.start()
	t_ready = time.time()
	if on_ready is not None:
		maybe = on_ready({"pool": pool.stats()})
		if asyncio.iscoroutine(maybe):
			await maybe
	try:
		await stopped.wait()
		await service.drain(drain_timeout)
	finally:
		reader.stop()
		stats = service.stats()
		stats.update(
			reply_messages=messages[0],
			reader_passes=reader.passes,
			pool=pool.stats(),
			up_s=round(time.time() - t_ready, 1),
		)
		await pool.close(timeout=5.0)
	return stats


class ToolServiceClient:
	"""An agent process's side of the tool service.

	``await call(uid, tool, args)`` sends a request and returns the tool's
	result, or raises ``ToolServiceError``. Requests made in one pass of the
	event loop go out together. A second call with a uid still in flight shares
	the first call's answer. ``listener(uid, total_s, at_service_s)`` is called
	once per answer: the time from asking to the answer, and of that, the time
	the service held the call.
	"""

	def __init__(
		self,
		requests: Any,
		replies: Any,
		index: int,
		*,
		listener: Callable[[str, float, float], None] | None = None,
	) -> None:
		self.requests = requests
		self.replies = replies
		self.index = index
		self.listener = listener
		self.pending: dict[str, asyncio.Future] = {}
		self.outbox: list = []
		self._flush_scheduled = False
		self._reader: _QueueReader | None = None
		self._loop: asyncio.AbstractEventLoop | None = None
		self._asked: dict[str, float] = {}
		self.sent = 0
		self.answered = 0
		self.errors = 0
		self.messages = 0

	def start(self) -> None:
		"""Start reading answers; call it on the event loop that makes calls."""
		self._loop = asyncio.get_running_loop()
		self._reader = _QueueReader(
			self.replies,
			self._loop,
			self._on_answers,
			name=f"flowgentic-replies-{self.index}",
		)
		self._reader.start()

	def stop(self) -> None:
		if self._reader is not None:
			self._reader.stop()

	async def call(self, uid: str, tool: str, args: dict) -> Any:
		if self._loop is None:
			raise RuntimeError("ToolServiceClient.start() was not called")
		future = self.pending.get(uid)
		if future is None:
			future = self.pending[uid] = self._loop.create_future()
			now = time.time()
			self._asked[uid] = now
			self.outbox.append((uid, tool, args, self.index, now))
			self.sent += 1
			if not self._flush_scheduled:
				self._flush_scheduled = True
				self._loop.call_soon(self._flush)
		return await asyncio.shield(future)

	def _flush(self) -> None:
		self._flush_scheduled = False
		items, self.outbox = self.outbox, []
		for i in range(0, len(items), CHUNK):
			self.requests.put(_pack(items[i : i + CHUNK]))
			self.messages += 1

	def _on_answers(self, items: list, t: float) -> None:
		for uid, ok, payload, (t_recv, _t_call, t_done) in items:
			future = self.pending.pop(uid, None)
			asked = self._asked.pop(uid, None)
			if future is None or future.done():
				continue
			self.answered += 1
			if self.listener is not None and asked is not None:
				self.listener(uid, t - asked, t_done - t_recv)
			if ok:
				future.set_result(payload)
			else:
				self.errors += 1
				future.set_exception(ToolServiceError(str(payload)))

	def stats(self) -> dict:
		return {
			"sent": self.sent,
			"answered": self.answered,
			"errors": self.errors,
			"unanswered": len(self.pending),
			"request_messages": self.messages,
		}
