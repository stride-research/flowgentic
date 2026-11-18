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
    result = await client.call(uid, "lookup", {"key": "x"}, idempotent=True)

``idempotent=True`` says the tool can safely run twice, as a lookup can: a
call that lost its connection is sent again, and a failure that left its
outcome unknown is not kept. Leave it out for tools with side effects.

A repeated uid is answered without calling again: while the first call runs,
the new asker joins its waiters; once it has ended, the asker gets the kept
answer at once. The service keeps each final answer until it stops, in memory
or in an ``answer_store``: a result, or a failure after which a call that is
not idempotent may have run. Any other failure, such as a call that never left
the queue, reaches its waiters but is not kept, so asking again calls again. A
``None`` on the request queue stops the service.

Requests and answers travel in batches of up to 512, pickled. Pickle is safe
here only because both ends are processes of the same job; the queues must not
be reachable from outside it.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging

# Both ends of the queues belong to one job.
import pickle  # nosec B403
import queue
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, MutableMapping
from typing import TYPE_CHECKING, Any

from pydantic_core import to_jsonable_python

if TYPE_CHECKING:  # the agent processes' side needs no MCP client
	from flowgentic.tools.mcp import SupervisedMcpPool

__all__ = ["ToolService", "ToolServiceClient", "ToolServiceError", "serve"]

log = logging.getLogger(__name__)

AnswerStore = MutableMapping[str, tuple[bool, Any]]

CHUNK = 512  # requests or answers per queue message

# How much longer than the service's call_timeout a client waits for an answer:
# the time a request and its answer spend in the queues and the readers.
_QUEUE_MARGIN_S = 30.0


def _pack(obj: Any) -> bytes:
	return pickle.dumps(obj, protocol=5)


def _unpack(data: bytes) -> Any:
	# Written by a process of the same job.
	return pickle.loads(data)  # nosec B301


class ToolServiceError(Exception):
	"""The service could not complete a call; the message says why."""


def mcp_data(result: Any) -> tuple[bool, Any]:
	"""``(ok, data)`` from an MCP ``CallToolResult``.

	The data is the structured content, if the result has some. Otherwise it
	is the text: each text block parsed as JSON, or ``{"text": text}`` if it is
	not JSON, and a list of them if there are several. A success with no
	content, or only empty text, is ``(True, None)``: the tool ran and returned
	nothing. A success whose only content is not text, such as an image, is a
	failure, since the agent would get nothing it can read.
	"""
	is_error = getattr(result, "is_error", None)
	if is_error is None:
		is_error = getattr(result, "isError", False)
	blocks = getattr(result, "content", None) or []
	texts = [getattr(b, "text", None) for b in blocks]
	if is_error:
		return False, " ".join(t for t in texts if t)[:500] or "the tool failed"
	structured = getattr(result, "structured_content", None)
	if structured is None:
		structured = getattr(result, "structuredContent", None)
	if isinstance(structured, dict):
		if set(structured) == {"result"}:
			return True, structured["result"]
		return True, structured
	values = [_text_data(t) for t in texts if isinstance(t, str) and t]
	if values:
		return True, values[0] if len(values) == 1 else values
	if all(isinstance(t, str) for t in texts):
		return True, None
	return False, f"no text or structured content in MCP result {result!r}"[:300]


def _text_data(text: str) -> Any:
	try:
		return json.loads(text)
	except ValueError:
		return {"text": text}


class _QueueReader(threading.Thread):
	"""Blocks on a queue and hands what arrived to the event loop, in batches.

	Each message is a pickled list of items, or ``None``, which stops the
	reader after ``on_stop`` runs on the loop. A message that cannot be
	unpickled is logged and skipped. If the reader fails anyway, ``on_stop``
	still runs, so that the loop's side stops and settles its callers.
	``alive``, if given, is asked every ``poll_s`` seconds while nothing
	arrives; once it says the writer is gone, the reader stops, sets ``gone``
	and runs ``on_stop``. A check that raises counts as the writer running: the
	first failure is logged as a warning, with its traceback, and later ones at
	debug level. ``stop()`` stops it within ``poll_s``, without ``on_stop``.
	"""

	poll_s = 1.0  # how long one wait on the queue lasts

	def __init__(
		self,
		q: Any,
		loop: asyncio.AbstractEventLoop,
		handler: Callable[[list, float], None],
		on_stop: Callable[[], None] | None = None,
		name: str = "flowgentic-queue-reader",
		max_messages: int = 256,
		alive: Callable[[], bool] | None = None,
	) -> None:
		super().__init__(name=name, daemon=True)
		self.q = q
		self.loop = loop
		self.handler = handler
		self.on_stop = on_stop
		self.max_messages = max_messages
		self.alive = alive
		self.gone = False
		self.check_failures = 0
		self.passes = 0
		self.skipped = 0  # messages that could not be unpickled
		self._halt = threading.Event()

	def stop(self) -> None:
		self._halt.set()

	def run(self) -> None:
		ended = False  # by a None on the queue, or by stop()
		try:
			ended = self._read()
		except Exception:
			log.exception("%s: the reader failed", self.name)
		finally:
			if not ended and self.on_stop is not None:
				try:
					self.loop.call_soon_threadsafe(self.on_stop)
				except RuntimeError:  # the loop is already closed
					pass

	def _read(self) -> bool:
		while not self._halt.is_set():
			try:
				raw = self.q.get(timeout=self.poll_s)
			except queue.Empty:
				if self.alive is not None and not self._writer_alive():
					self.gone = True
					log.error("%s: the process that answers is not running", self.name)
					return False  # not ended by a message: run() calls on_stop
				continue
			except Exception as exc:  # a queue error must not end the reader silently
				log.warning("%s: get failed: %r", self.name, exc)
				time.sleep(0.1)
				continue
			if self._halt.is_set():
				return True
			raws = [raw]
			while len(raws) < self.max_messages:
				try:
					raws.append(self.q.get(block=False))
				except Exception:  # queue.Empty, or a runtime's own equivalent
					break
			items: list = []
			stop = False
			for r in raws:
				try:
					message = None if r is None else _unpack(r)
				except Exception as exc:
					# Its callers get no answer; everyone else's requests go on.
					self.skipped += 1
					log.error(
						"%s: skipped a message that cannot be unpickled: %r",
						self.name,
						exc,
					)
					continue
				if message is None:
					stop = True
					break
				items.extend(message)
			self.passes += 1
			if items:
				self.loop.call_soon_threadsafe(self.handler, items, time.time())
			if stop:
				if self.on_stop is not None:
					self.loop.call_soon_threadsafe(self.on_stop)
				return True
		return True

	def _writer_alive(self) -> bool:
		try:
			return bool(self.alive())  # type: ignore[misc]
		except Exception as exc:  # a broken check must not end the reader
			self.check_failures += 1
			first = self.check_failures == 1
			log.log(
				logging.WARNING if first else logging.DEBUG,
				"%s: the liveness check failed (%s: %s); taken as still running",
				self.name,
				type(exc).__name__,
				exc,
				exc_info=first,
			)
			return True


class _Call:
	__slots__ = ("t_recv", "waiters")

	def __init__(self, src: int, t_recv: float) -> None:
		self.waiters = [src]
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
		*,
		answer_store: AnswerStore | None = None,
	) -> None:
		self.pool = pool
		self.out = out
		self.call_timeout = call_timeout
		self.listener = listener
		self.calls: dict[str, _Call | tuple[bool, Any]] = {}
		self.answer_store = answer_store
		self.unique = 0
		self.outbox: dict[int, list] = defaultdict(list)
		self._flush_scheduled = False
		self.inflight = 0
		self.received = 0
		self.repeats_running = 0
		self.repeats_done = 0
		self.retryable = 0  # failures answered but not kept
		self.undelivered = 0  # answers whose process could not be reached
		self.ok: Counter[str] = Counter()
		self.failed: Counter[str] = Counter()
		self.failures: list[str] = []
		self._tasks: set[asyncio.Task] = set()

	def on_requests(self, requests: list, t_recv: float) -> None:
		loop = asyncio.get_running_loop()
		for uid, tool, args, src, _t_ask, *options in requests:
			self.received += 1
			call = self.calls.get(uid)
			if call is None and self.answer_store is not None:
				try:
					call = self.answer_store.get(uid)
				except Exception as exc:
					# An unknown replay status must never trigger a tool execution.
					self._queue(
						src, (uid, False, f"answer lookup failed: {exc}", (t_recv,) * 3)
					)
					continue
			if call is None:
				self.unique += 1
				call = self.calls[uid] = _Call(src, t_recv)
				self.inflight += 1
				task = loop.create_task(
					self._call(uid, call, tool, args, bool(options and options[0]))
				)
				self._tasks.add(task)
				task.add_done_callback(self._tasks.discard)
			elif isinstance(call, _Call):
				self.repeats_running += 1
				call.waiters.append(src)
			else:
				# answered at once: its own times, not the first call's
				self.repeats_done += 1
				ok, payload = call
				self._queue(src, (uid, ok, payload, (t_recv, t_recv, t_recv)))

	async def _call(
		self, uid: str, call: _Call, tool: str, args: dict, idempotent: bool
	) -> None:
		t_call = time.time()
		# Final: the server answered, or a call that must not run twice may have
		# run. Any other failure reaches this attempt's waiters but is not kept,
		# so that asking again calls again.
		final = True
		try:
			result = await self.pool.call(
				tool, args, timeout=self.call_timeout, idempotent=idempotent
			)
			ok, payload = mcp_data(result)
		except asyncio.CancelledError as exc:
			ok, payload = False, "tool service stopped during the call"
			final = not idempotent and getattr(exc, "may_have_run", True)
		except Exception as exc:  # reported to the agent
			ok, payload = False, f"{type(exc).__name__}: {exc}"[:500]
			final = not idempotent and getattr(exc, "may_have_run", True)
		t_done = time.time()
		self.inflight -= 1
		(self.ok if ok else self.failed)[tool] += 1
		if not ok and len(self.failures) < 10:
			self.failures.append(f"{uid}: {payload}"[:300])
		if self.listener is not None:
			try:
				self.listener(uid, tool, call.t_recv, t_call, t_done, ok)
			except Exception:
				log.exception("tool service listener failed for %s", uid)
		if not final:
			self.retryable += 1
			del self.calls[uid]
		elif self.answer_store is None:
			self.calls[uid] = (ok, payload)
		else:
			try:
				self.answer_store[uid] = (ok, payload)
			except Exception:
				log.exception(
					"could not save answer for %s; retaining it in memory", uid
				)
				self.calls[uid] = (ok, payload)
			else:
				del self.calls[uid]
		answer = (uid, ok, payload, (call.t_recv, t_call, t_done))
		for src in call.waiters:
			self._queue(src, answer)

	def _queue(self, src: int, answer: tuple) -> None:
		self.outbox[src].append(answer)
		if not self._flush_scheduled:
			self._flush_scheduled = True
			asyncio.get_running_loop().call_soon(self.flush)

	def flush(self) -> None:
		self._flush_scheduled = False
		outbox, self.outbox = self.outbox, defaultdict(list)
		for src, items in outbox.items():
			# One process's answers that cannot be delivered lose no other's.
			try:
				self.out(src, items)
			except Exception:
				self.undelivered += len(items)
				log.exception(
					"tool service: could not answer process %s; %d answers lost",
					src,
					len(items),
				)

	async def drain(self, timeout: float) -> None:
		end = time.monotonic() + timeout
		while self.inflight and time.monotonic() < end:
			await asyncio.sleep(0.05)
		self.flush()

	def stats(self) -> dict:
		"""Requests received, calls made, repeats, failures not kept, answers
		that could not be delivered, and outcomes per tool."""
		return {
			"received": self.received,
			"unique": self.unique,
			"repeats_running": self.repeats_running,
			"repeats_done": self.repeats_done,
			"retryable": self.retryable,
			"undelivered": self.undelivered,
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
	answer_store: AnswerStore | None = None,
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
	unknown = [0]  # requests from a process index with no reply queue

	def out(src: int, items: list) -> None:
		for i in range(0, len(items), CHUNK):
			replies[src].put(_pack(items[i : i + CHUNK]))
			messages[0] += 1

	def on_requests(requests: list, t_recv: float) -> None:
		# A request whose answer could not be delivered is refused on arrival:
		# no tool runs for nobody.
		known = []
		for request in requests:
			src = (
				request[3] if isinstance(request, tuple) and len(request) > 3 else None
			)
			if type(src) is int and 0 <= src < len(replies):
				known.append(request)
			else:
				unknown[0] += 1
				log.error(
					"tool service: refused a request from process %r, which has "
					"no reply queue",
					src,
				)
		service.on_requests(known, t_recv)

	try:
		await pool.start(
			wait=len(pool.servers), timeout=connect_timeout, all_servers=True
		)
	except BaseException:
		_stop_all(replies)
		raise
	service = ToolService(pool, out, call_timeout, listener, answer_store=answer_store)
	stopped = asyncio.Event()
	reader = _QueueReader(
		requests,
		loop,
		on_requests,
		on_stop=stopped.set,
		name="flowgentic-requests",
	)
	reader.start()
	t_ready = time.time()
	try:
		if on_ready is not None:
			maybe = on_ready({"pool": pool.stats()})
			if inspect.isawaitable(maybe):
				await maybe
		await stopped.wait()
		await service.drain(drain_timeout)
	finally:
		reader.stop()
		await pool.close(timeout=5.0)
		if service._tasks:
			for task in service._tasks:
				task.cancel()
			await asyncio.gather(*service._tasks, return_exceptions=True)
		service.flush()
		_stop_all(replies)
		stats = service.stats()
		stats.update(
			unknown_sources=unknown[0],
			reply_messages=messages[0],
			reader_passes=reader.passes,
			skipped_messages=reader.skipped,
			pool=pool.stats(),
			up_s=round(time.time() - t_ready, 1),
		)
	return stats


def _stop_all(replies: list) -> None:
	"""Tell every agent process that the service has stopped. A reply queue
	that fails does not keep the others from being told."""
	for src, reply in enumerate(replies):
		try:
			reply.put(_pack(None))
		except Exception:
			log.exception(
				"tool service: could not tell process %s that it stopped", src
			)


class ToolServiceClient:
	"""An agent process's side of the tool service.

	``await call(uid, tool, args)`` sends a request and returns the tool's
	result, or raises ``ToolServiceError``, also once the service has stopped.
	Pass ``idempotent=True`` only for a tool that can safely run twice, such as
	a lookup. Requests made in one pass of the event loop go out together. A
	second call with a uid still in flight shares the first call's answer.

	``args`` travel as JSON values, as MCP takes them: pydantic models, enums
	and other values with a JSON form are converted before they are sent, so
	the service never needs the agent's classes. Arguments with no JSON form
	fail their own call at once.

	A call with no answer within ``timeout`` seconds fails, so an agent does
	not wait forever on a service that is gone. Pass ``call_timeout``, the one
	``serve`` runs with (120 s by default), and leave ``timeout`` out: it is then
	``call_timeout`` and 30 s for the queues, 150 s by default. The service
	ends every call within its ``call_timeout``, resends included, so a client
	that waits longer never gives up on a call the service still makes; a
	``timeout`` not above ``call_timeout`` is refused. Asking again with the
	same uid sends the request again.

	``alive``, if given, tells whether the service is still running. The
	client asks it from its reader thread, in the agent process, about once a
	second while no answer arrives; once it returns false, the client fails
	every waiting call and stops. For a service on the same node, test its
	pid: ``os.kill(pid, 0)`` raises ``ProcessLookupError`` once the process is
	gone and its parent has reaped it. A ``multiprocessing.Process``'s
	``is_alive`` works only in the process that started the service. A check
	that raises counts as the service running, and is logged once as a
	warning.

	``listener(uid, total_s, at_service_s)`` is called once per answer: the
	time from asking to the answer, and of that, the time the service held the
	call.
	"""

	def __init__(
		self,
		requests: Any,
		replies: Any,
		index: int,
		*,
		listener: Callable[[str, float, float], None] | None = None,
		timeout: float | None = None,
		call_timeout: float = 120.0,
		alive: Callable[[], bool] | None = None,
	) -> None:
		if not call_timeout > 0:
			raise ValueError("call_timeout must be positive")
		if timeout is None:
			timeout = call_timeout + _QUEUE_MARGIN_S
		elif not timeout > 0:
			raise ValueError("timeout must be positive")
		elif not timeout > call_timeout:
			raise ValueError(
				f"timeout ({timeout:g} s) must be above call_timeout "
				f"({call_timeout:g} s), the service's limit on a call: the client "
				"would give up on calls the service still makes"
			)
		self.requests = requests
		self.replies = replies
		self.index = index
		self.listener = listener
		self.timeout = timeout
		self.alive = alive
		self.pending: dict[str, asyncio.Future] = {}
		self.outbox: list = []
		self._flush_scheduled = False
		self._reader: _QueueReader | None = None
		self._loop: asyncio.AbstractEventLoop | None = None
		self._stopped = False
		self._asked: dict[str, float] = {}
		self._deadlines: dict[str, asyncio.TimerHandle] = {}
		self.sent = 0
		self.answered = 0
		self.errors = 0
		self.expired = 0
		self.messages = 0

	def start(self) -> None:
		"""Start reading answers; call it on the event loop that makes calls."""
		if self._reader is not None:
			raise RuntimeError("ToolServiceClient already started")
		self._loop = asyncio.get_running_loop()
		self._reader = _QueueReader(
			self.replies,
			self._loop,
			self._on_answers,
			on_stop=self._on_service_stop,
			name=f"flowgentic-replies-{self.index}",
			alive=self.alive,
		)
		self._reader.start()

	def stop(self) -> None:
		"""Stop reading answers, within a second, and fail the calls still
		waiting. It runs on its own once the service has stopped."""
		self._halt("tool service client stopped")

	def _on_service_stop(self) -> None:
		if self._reader is not None and self._reader.gone:
			self._halt("tool service client stopped: the tool service is not running")
		else:
			self._halt("tool service client stopped")

	def _halt(self, reason: str) -> None:
		if self._reader is not None:
			self._reader.stop()
		self._stopped = True
		self._loop = None
		self.outbox.clear()
		for handle in self._deadlines.values():
			handle.cancel()
		self._deadlines.clear()
		for future in self.pending.values():
			if not future.done():
				future.set_exception(ToolServiceError(reason))
		self.pending.clear()
		self._asked.clear()

	async def call(
		self, uid: str, tool: str, args: dict, *, idempotent: bool = False
	) -> Any:
		if self._stopped:
			raise ToolServiceError("tool service stopped")
		if self._loop is None:
			raise RuntimeError("ToolServiceClient.start() was not called")
		future = self.pending.get(uid)
		if future is None:
			try:
				args = to_jsonable_python(args)
			except Exception as exc:
				raise ToolServiceError(
					f"could not send request: its arguments are not JSON: {exc}"
				) from None
			future = self.pending[uid] = self._loop.create_future()
			self._deadlines[uid] = self._loop.call_later(
				self.timeout, self._expire, uid
			)
			now = time.time()
			self._asked[uid] = now
			self.outbox.append((uid, tool, args, self.index, now, idempotent))
			self.sent += 1
			if not self._flush_scheduled:
				self._flush_scheduled = True
				self._loop.call_soon(self._flush)
		return await asyncio.shield(future)

	def _settle(self, uid: str) -> tuple[asyncio.Future | None, float | None]:
		"""Forget a call: its future and when it was asked."""
		handle = self._deadlines.pop(uid, None)
		if handle is not None:
			handle.cancel()
		return self.pending.pop(uid, None), self._asked.pop(uid, None)

	def _expire(self, uid: str) -> None:
		self._deadlines.pop(uid, None)
		future, _ = self._settle(uid)
		if future is None or future.done():
			return
		self.expired += 1
		future.set_exception(
			ToolServiceError(
				f"no answer from the tool service within {self.timeout:g} s"
			)
		)

	def _flush(self) -> None:
		self._flush_scheduled = False
		items, self.outbox = self.outbox, []
		for i in range(0, len(items), CHUNK):
			self._send_batch(items[i : i + CHUNK])

	def _send_batch(self, batch: list) -> None:
		try:
			try:
				message = _pack(batch)
			except Exception:
				if len(batch) == 1:
					raise
				# Packing failed before delivery. Isolate the invalid request.
				for item in batch:
					self._send_batch([item])
				return
			self.requests.put(message)
		except Exception as exc:
			for uid, *_ in batch:
				future, _ = self._settle(uid)
				if future is not None and not future.done():
					future.set_exception(
						ToolServiceError(f"could not send request: {exc}")
					)
		else:
			self.messages += 1

	def _on_answers(self, items: list, t: float) -> None:
		for uid, ok, payload, (t_recv, _t_call, t_done) in items:
			future, asked = self._settle(uid)
			if future is None or future.done():
				continue  # answered after its deadline, or after a stop
			self.answered += 1
			if self.listener is not None and asked is not None:
				try:
					self.listener(uid, t - asked, t_done - t_recv)
				except Exception:
					log.exception("tool service client listener failed for %s", uid)
			if ok:
				future.set_result(payload)
			else:
				self.errors += 1
				future.set_exception(ToolServiceError(str(payload)))

	def stats(self) -> dict:
		"""Requests sent and answered, errors, calls that reached their
		deadline, and calls still unanswered."""
		return {
			"sent": self.sent,
			"answered": self.answered,
			"errors": self.errors,
			"expired": self.expired,
			"unanswered": len(self.pending),
			"request_messages": self.messages,
		}
