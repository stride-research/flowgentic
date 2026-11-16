"""A supervised pool of MCP clients, the tool service's MCP client.

Why supervision. The Python ``mcp`` SDK's Streamable HTTP client runs each
request's POST as a child of an anyio task group whose cancel scope encloses
the body of ``async with Client(...)``. In 2.3.0 a network error on one POST (a
reset, a server closing the connection, a refused connect, an HTTP read
timeout) escapes that request into the task group, which then cancels its
scope: every call in flight on the client gets a bare ``CancelledError``, the
task that entered the client is cancelled too, and leaving the ``async with``
raises an ``ExceptionGroup``. Upstream tracks it as
modelcontextprotocol/python-sdk#3522 and #3560. Once it is fixed, any other
unexpected error in the transport still ends the same way, since that is how
anyio task groups work. So no client is entered in a task whose cancellation
matters: each one gets a supervisor task of its own.

What the pool guarantees:

- When a client's connection fails, only the calls that client had in flight fail, with
  `McpConnectionLost`. Queued calls wait for another client. The caller's task is never cancelled.
- The supervisor reconnects with exponential backoff and jitter. The backoff resets once a
  connection has served a call, or stayed up for `Backoff.reset_after` seconds.
- Each client carries at most `per_client` calls at once: one worker per slot, all pulling from
  one queue. With stateless servers any client can carry any call.
- Given several servers, client `i` connects to server `i mod servers`. The clients share the one
  queue, so a faster server takes more calls, and a server that is down holds none.
- When no client has been connected for `unavailable_after` seconds, queued and new calls fail at
  once with `McpUnavailable`, until a client reconnects.
- A call's `timeout` covers its wait in the queue and the call itself. A caller that stops waiting
  (timeout or cancellation) also stops its call, which frees the slot.
- `close()` stops intake, lets calls finish for up to `drain` seconds, fails the rest with
  `McpPoolClosed`, and waits up to `timeout` seconds for the clients to exit. It never raises.

A call that fails with `McpConnectionLost` may or may not have run on the server. It is sent again
only when the caller says the tool is safe to repeat (`idempotent=True`), at most `resends` times.

With an SDK that contains transport errors per request (the fix proposed for python-sdk#3522), a
dropped connection reaches the pool as `MCPError(CONNECTION_CLOSED)` on that call alone; the pool
treats it the same way, and restarts a client after `max_losses` such losses in a row.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections import Counter, deque
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

try:
	from mcp import MCPError
	from mcp_types import CONNECTION_CLOSED
except ModuleNotFoundError as exc:
	if exc.name not in {"mcp", "mcp_types"}:
		raise
	raise ModuleNotFoundError(
		"flowgentic.tools.mcp needs the MCP SDK: install flowgentic[mcp]",
		name=exc.name,
	) from exc

import anyio  # the MCP SDK's own dependency

__all__ = [
	"Backoff",
	"McpCallError",
	"McpConnectionLost",
	"McpPoolClosed",
	"McpUnavailable",
	"SupervisedMcpPool",
]

log = logging.getLogger(__name__)


class McpCallError(Exception):
	"""A call the pool could not complete."""


class McpConnectionLost(McpCallError):
	"""The call was in flight on a connection that failed. The server may or may not have run it."""


class McpUnavailable(McpCallError):
	"""No client has been connected for `unavailable_after` seconds."""


class McpPoolClosed(McpCallError):
	"""The pool closed before the call finished."""


@dataclass(frozen=True)
class Backoff:
	"""The wait before reconnection attempt `n` (from 0): `initial × factor**n`, capped at
	`maximum`, times a random factor in `[1 - jitter, 1]` so that clients do not reconnect in step."""

	initial: float = 0.1
	factor: float = 2.0
	maximum: float = 30.0
	jitter: float = 0.5
	reset_after: float = 30.0

	def delay(self, attempt: int, rng: random.Random) -> float:
		base = min(self.maximum, self.initial * self.factor ** min(attempt, 64))
		# not for security: jitter, so clients do not reconnect in step
		return base * (1.0 - self.jitter * rng.random())  # noqa: S311  # nosec B311


@dataclass(eq=False)
class _Call:
	name: str
	args: dict[str, Any]
	fut: asyncio.Future
	deadline: float  # event-loop time
	resends: int
	client: int = -1  # the client that carried it last


@dataclass(eq=False)
class _Conn:
	"""One live connection of client `index`."""

	index: int
	client: Any
	since: float
	ok: int = 0  # calls answered on this connection
	losses: int = 0  # calls in a row that lost their connection, with no answer between


class _Unhealthy(Exception):
	"""Calls keep losing their connection while the client stays up: restart it."""


Connect = Callable[[], AbstractAsyncContextManager[Any]]


def _url_connect(url: str) -> Connect:
	def connect() -> AbstractAsyncContextManager[Any]:
		from mcp import Client

		return Client(url)

	connect.__name__ = url
	return connect


class SupervisedMcpPool:
	"""A pool of `clients` MCP clients, `per_client` calls in flight on each.

	`server` is a Streamable HTTP URL, or a function that returns a new client to enter (anything
	with `call_tool(name, args, read_timeout_seconds=...)`), e.g. `lambda: Client(transport)` with
	an `httpx2.AsyncClient` of your own; or a list of either, one per server process, which the
	clients take in turn (there must be at least as many clients as servers).

	    async with SupervisedMcpPool("http://127.0.0.1:8000/mcp", clients=8, per_client=8) as pool:
	        result = await pool.call("lookup", {"key": "x"}, timeout=30)
	"""

	def __init__(
		self,
		server: str | Connect | Sequence[str | Connect],
		*,
		clients: int = 8,
		per_client: int = 8,
		call_timeout: float = 120.0,
		connect_timeout: float = 30.0,
		backoff: Backoff = Backoff(),
		unavailable_after: float = 10.0,
		max_losses: int = 3,
		resends: int = 1,
		seed: int | None = None,
	) -> None:
		if clients < 1 or per_client < 1:
			raise ValueError("clients and per_client must be at least 1")
		servers = (
			[server] if isinstance(server, str) or callable(server) else list(server)
		)
		if not servers:
			raise ValueError("no MCP server given")
		if clients < len(servers):
			raise ValueError(
				f"{clients} clients cannot reach {len(servers)} servers: give at least one per server"
			)
		self._connects: list[Connect] = [
			_url_connect(s) if isinstance(s, str) else s for s in servers
		]
		names = [getattr(c, "__name__", repr(c)) for c in self._connects]
		if len(set(names)) < len(names):
			names = [f"{name}#{k}" for k, name in enumerate(names)]
		self.servers = names
		self.server = (
			names[0] if len(names) == 1 else f"{len(names)} servers ({names[0]}, ...)"
		)
		self.clients = clients
		self.per_client = per_client
		self.call_timeout = call_timeout
		self.connect_timeout = connect_timeout
		self.backoff = backoff
		self.unavailable_after = unavailable_after
		self.max_losses = max_losses
		self.resends = resends
		self._rng = random.Random(seed)  # noqa: S311  # nosec B311
		self._queue: asyncio.Queue[_Call] = asyncio.Queue()
		self._conns: dict[int, _Conn] = {}
		self._supervisors: list[asyncio.Task] = []
		self._loop: asyncio.AbstractEventLoop | None = None
		self._started = False
		self._closing = False
		self._closed = asyncio.Event()
		self._changed = asyncio.Event()
		self._down_since: float | None = None
		self._unavailable_timer: asyncio.TimerHandle | None = None
		self._in_flight = 0
		self.counts: Counter[str] = Counter()
		self.served = [0] * len(self._connects)  # calls answered, per server
		self.errors: deque[str] = deque(maxlen=20)

	# --- the caller's side ---------------------------------------------------------------------

	async def __aenter__(self) -> SupervisedMcpPool:
		await self.start()
		return self

	async def __aexit__(self, *exc: object) -> None:
		await self.close()

	async def start(self, wait: int = 1, timeout: float | None = None) -> None:
		"""Starts one supervisor per client, and waits until `wait` clients are connected (0: do
		not wait). If they are not connected within `timeout` (default `connect_timeout`), closes
		the pool and raises `McpUnavailable`."""
		if self._started:
			raise RuntimeError("the pool was already started")
		self._started = True
		self._loop = asyncio.get_running_loop()
		self._mark_down()
		self._supervisors = [
			asyncio.create_task(self._supervise(i), name=f"mcp-client-{i}")
			for i in range(self.clients)
		]
		if wait:
			try:
				await asyncio.wait_for(
					self._wait_connected(min(wait, self.clients)),
					timeout or self.connect_timeout,
				)
			except TimeoutError:
				await self.close()
				raise McpUnavailable(
					f"no MCP client connected to {self.server} within {timeout or self.connect_timeout} s: {self.last_error()}"
				) from None

	async def call(
		self,
		name: str,
		args: dict[str, Any] | None = None,
		*,
		timeout: float | None = None,
		idempotent: bool = False,
	) -> Any:
		"""Calls tool `name` and returns its result (a tool's own errors are results, with
		`is_error` set). Raises `McpConnectionLost`, `McpUnavailable`, `McpPoolClosed`,
		`TimeoutError` after `timeout` seconds (default `call_timeout`) from now, or the server's
		`MCPError`."""
		if not self._started or self._closing:
			raise McpPoolClosed("the MCP pool is not running")
		if self._unavailable():
			self.counts["unavailable"] += 1
			raise McpUnavailable(
				f"no MCP client connected to {self.server} for {self.unavailable_after} s: {self.last_error()}"
			)
		loop = asyncio.get_running_loop()
		timeout = self.call_timeout if timeout is None else timeout
		call = _Call(
			name,
			dict(args or {}),
			loop.create_future(),
			loop.time() + timeout,
			self.resends if idempotent else 0,
		)
		self._queue.put_nowait(call)
		try:
			async with asyncio.timeout_at(call.deadline):
				# Cancelling this await cancels `fut`, which stops the call.
				return await call.fut
		except TimeoutError:
			self.counts["timed_out"] += 1
			raise TimeoutError(
				f"MCP call {name!r} took more than {timeout} s"
			) from None

	async def close(self, drain: float = 0.0, timeout: float = 10.0) -> None:
		"""Stops intake; lets queued and in-flight calls finish for up to `drain` seconds; fails
		the rest with `McpPoolClosed`; waits up to `timeout` seconds for the clients to exit.
		Never raises; a second call waits for the first."""
		if self._closing:
			await self._closed.wait()
			return
		self._closing = True
		loop = asyncio.get_running_loop()
		if drain > 0:
			end = loop.time() + drain
			while (
				(self._in_flight or not self._queue.empty())
				and self._conns
				and loop.time() < end
			):
				await asyncio.sleep(0.01)
		self._fail_queued(McpPoolClosed("the MCP pool closed"), "closed")
		if self._unavailable_timer is not None:
			self._unavailable_timer.cancel()
		for task in self._supervisors:
			task.cancel()
		if self._supervisors:
			done, pending = await asyncio.wait(self._supervisors, timeout=timeout)
			for task in done:
				if not task.cancelled() and task.exception() is not None:
					log.warning(
						"MCP client %s ended with %s",
						task.get_name(),
						_describe(task.exception()),
					)
			for task in pending:
				log.warning(
					"MCP client %s still closing after %s s; left behind",
					task.get_name(),
					timeout,
				)
		self._closed.set()

	def stats(self) -> dict[str, Any]:
		"""The pool's state and counters, and its last errors."""
		return {
			"server": self.server,
			"servers": len(self.servers),
			"served_per_server": dict(zip(self.servers, self.served)),
			"clients": self.clients,
			"per_client": self.per_client,
			"connected": len(self._conns),
			"in_flight": self._in_flight,
			"queued": self._queue.qsize(),
			**{k: self.counts[k] for k in sorted(self.counts)},
			"errors": list(self.errors),
		}

	def last_error(self) -> str:
		"""The most recent client or call error, for messages."""
		return self.errors[-1] if self.errors else "no error recorded"

	# --- supervision ---------------------------------------------------------------------------

	async def _supervise(self, i: int) -> None:
		"""Keeps client `i` connected: connect, serve, and after a failure, back off and reconnect."""
		attempt = 0
		while not self._closing:
			conn: _Conn | None = None
			try:
				async with contextlib.AsyncExitStack() as stack:
					# asyncio's timeout, not anyio's: the client's own cancel scopes stay open after
					# this block, and anyio scopes must close in the order they were opened.
					async with asyncio.timeout(self.connect_timeout):
						client = await stack.enter_async_context(
							self._connects[i % len(self._connects)]()
						)
					conn = _Conn(i, client, self._loop.time())
					self._up(conn, attempt)
					await self._serve(conn)
			except BaseException as exc:
				if self._closing:
					return
				if isinstance(
					exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
				):
					raise  # cancelled from outside the pool
				self._note(i, conn, exc)
			finally:
				if conn is not None:
					self._down(conn)
			if self._closing:
				return
			if conn is not None and (
				conn.ok > 0
				or self._loop.time() - conn.since >= self.backoff.reset_after
			):
				attempt = 0
			delay = self.backoff.delay(attempt, self._rng)
			attempt += 1
			try:
				await asyncio.sleep(delay)
			except asyncio.CancelledError:
				if self._closing:
					return
				raise

	async def _serve(self, conn: _Conn) -> None:
		"""Runs the connection's workers until the connection fails (or the pool closes)."""
		async with asyncio.TaskGroup() as tg:
			for k in range(self.per_client):
				tg.create_task(
					self._worker(conn), name=f"mcp-client-{conn.index}-slot-{k}"
				)

	async def _worker(self, conn: _Conn) -> None:
		while True:
			call = await self._queue.get()
			if call.fut.done():
				continue  # the caller stopped waiting while it was queued
			remaining = call.deadline - self._loop.time()
			if remaining <= 0:
				continue  # the caller's own timeout settles it
			call.client = conn.index
			self._in_flight += 1
			try:
				result = await self._invoke(conn, call, remaining)
			except asyncio.CancelledError:
				# With mcp 2.3.0, the client's transport failed and cancelled its scope, which
				# holds this worker; or the pool is closing. Either way, this connection ends.
				self._lose(call, self._closing)
				raise
			except Exception as exc:
				if isinstance(exc, MCPError) and exc.code == CONNECTION_CLOSED:
					self._lose(call, False)
					conn.losses += 1
					if conn.losses >= self.max_losses:
						raise _Unhealthy(
							f"{conn.losses} calls in a row lost their connection: {exc.message}"
						) from exc
				else:
					conn.losses = 0
					self._settle(call, exc=exc)
			else:
				if not call.fut.done():
					conn.losses = 0
					conn.ok += 1
					self.served[conn.index % len(self.served)] += 1
				self._settle(call, result=result)
			finally:
				self._in_flight -= 1

	async def _invoke(self, conn: _Conn, call: _Call, remaining: float) -> Any:
		"""Runs one call; stops it if its caller stops waiting (`fut` cancelled)."""
		scope = anyio.CancelScope()

		def caller_gone(fut: asyncio.Future) -> None:
			if fut.cancelled():
				scope.cancel()

		call.fut.add_done_callback(caller_gone)
		try:
			with scope:
				return await conn.client.call_tool(
					call.name, call.args, read_timeout_seconds=remaining
				)
			self.counts["abandoned"] += (
				1  # only reached when the caller stopped waiting
			)
			return None
		finally:
			call.fut.remove_done_callback(caller_gone)

	# --- bookkeeping ---------------------------------------------------------------------------

	def _settle(
		self, call: _Call, *, result: Any = None, exc: BaseException | None = None
	) -> None:
		if call.fut.done():
			return
		if exc is not None:
			self.counts["error"] += 1
			call.fut.set_exception(exc)
		else:
			self.counts[
				"tool_error" if getattr(result, "is_error", False) else "ok"
			] += 1
			call.fut.set_result(result)

	def _lose(self, call: _Call, closing: bool) -> None:
		"""Call `call` lost its connection: send it again if allowed, else fail it."""
		if call.fut.done():
			return
		if closing:
			self.counts["closed"] += 1
			call.fut.set_exception(
				McpPoolClosed("the MCP pool closed while the call was in flight")
			)
		elif call.resends > 0 and self._loop.time() < call.deadline:
			call.resends -= 1
			self.counts["resent"] += 1
			self._queue.put_nowait(call)
		else:
			self.counts["connection_lost"] += 1
			call.fut.set_exception(
				McpConnectionLost(
					f"MCP client {call.client}'s connection failed while {call.name!r} was in flight"
				)
			)

	def _fail_queued(self, exc: McpCallError, count: str) -> None:
		while not self._queue.empty():
			call = self._queue.get_nowait()
			if not call.fut.done():
				self.counts[count] += 1
				call.fut.set_exception(exc)

	def _up(self, conn: _Conn, attempt: int) -> None:
		self._conns[conn.index] = conn
		self._down_since = None
		if self._unavailable_timer is not None:
			self._unavailable_timer.cancel()
			self._unavailable_timer = None
		if attempt:
			log.info(
                "MCP client %d reconnected to %s after %d attempt(s)",
                conn.index, self.servers[conn.index % len(self.servers)], attempt,
            )  # fmt: skip
		self._notify()

	def _down(self, conn: _Conn) -> None:
		if self._conns.get(conn.index) is conn:
			del self._conns[conn.index]
		if not self._conns and not self._closing:
			self._mark_down()
		self._notify()

	def _mark_down(self) -> None:
		self._down_since = self._loop.time()
		if self._unavailable_timer is not None:
			self._unavailable_timer.cancel()
		self._unavailable_timer = self._loop.call_later(
			self.unavailable_after, self._check_unavailable
		)

	def _unavailable(self) -> bool:
		return (
			not self._conns
			and self._down_since is not None
			and self._loop.time() - self._down_since >= self.unavailable_after
		)

	def _check_unavailable(self) -> None:
		self._unavailable_timer = None
		if self._unavailable() and not self._closing:
			self._fail_queued(
				McpUnavailable(
					f"no MCP client connected to {self.server} for {self.unavailable_after} s: {self.last_error()}"
				),
				"unavailable",
			)

	def _note(self, i: int, conn: _Conn | None, exc: BaseException) -> None:
		what = _describe(exc)
		self.counts["restarts" if conn is not None else "connect_failures"] += 1
		self.errors.append(f"{time.strftime('%H:%M:%S')} client {i}: {what}"[:300])
		server = self.servers[i % len(self.servers)]
		if conn is not None:
			log.warning("MCP client %d lost its connection to %s: %s", i, server, what)
		else:
			log.info("MCP client %d could not connect to %s: %s", i, server, what)

	def _notify(self) -> None:
		self._changed.set()
		self._changed = asyncio.Event()

	async def _wait_connected(self, n: int) -> None:
		while len(self._conns) < n:
			await self._changed.wait()


def _describe(exc: BaseException) -> str:
	"""The innermost errors of an exception group, or the exception itself."""
	subs = getattr(exc, "exceptions", None)
	if subs:
		return "; ".join(_describe(e) for e in subs[:3])
	return f"{type(exc).__name__}: {exc}"
