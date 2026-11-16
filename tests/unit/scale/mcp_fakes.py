"""A stand-in for an MCP server and the SDK's client, built the way mcp 2.3.0's client is built.

Each connection is an anyio task group whose cancel scope encloses the body of `async with`, as in
`mcp.client.streamable_http.streamable_http_client`. When the connection breaks, a task in that
group raises, so the group cancels the body (the pool's supervisor and its workers) and leaving
the `async with` raises an `ExceptionGroup`: the failure the pool is built for.

Tools, by name: `echo` returns its arguments; `slow` sleeps `s` seconds; `hang` never returns;
`break` breaks the connection that carries it while it is in flight; `lost` fails as a fixed SDK
would report a dropped connection (`MCPError(CONNECTION_CLOSED)`); `bad` fails with a server error;
`tool_error` returns a result with `is_error` set; `flaky` breaks its connection the first time
it sees a `key`, and answers after that.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import anyio
from mcp import MCPError
from mcp_types import CONNECTION_CLOSED, INVALID_PARAMS, REQUEST_TIMEOUT


@dataclass
class Result:
    content: Any
    is_error: bool = False


@dataclass(eq=False)
class FakeConn:
    server: FakeServer
    index: int
    broken: anyio.Event = field(default_factory=anyio.Event)
    active: int = 0
    max_active: int = 0
    calls: int = 0
    names: list[str] = field(default_factory=list)
    cancelled: int = 0
    closed: bool = False

    async def call_tool(self, name: str, args: dict, read_timeout_seconds: float | None = None) -> Result:
        self.calls += 1
        self.names.append(name)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.server.active += 1
        self.server.max_active = max(self.server.max_active, self.server.active)
        try:
            with anyio.fail_after(read_timeout_seconds):
                return await self._run(name, args)
        except TimeoutError:
            raise MCPError(REQUEST_TIMEOUT, f"Request {name!r} timed out") from None
        except anyio.get_cancelled_exc_class():
            self.cancelled += 1
            raise
        finally:
            self.active -= 1
            self.server.active -= 1

    async def _run(self, name: str, args: dict) -> Result:
        if name == "echo":
            return Result(args)
        if name == "slow":
            await anyio.sleep(args["s"])
            return Result(args)
        if name == "hang":
            await anyio.sleep_forever()
        if name == "break":
            await anyio.sleep(args.get("after", 0.0))
            self.broken.set()
            await anyio.sleep_forever()
        if name == "flaky":
            if args["key"] not in self.server.seen:
                self.server.seen.add(args["key"])
                self.broken.set()
                await anyio.sleep_forever()
            return Result(args)
        if name == "lost":
            raise MCPError(CONNECTION_CLOSED, "POST failed: ReadError")
        if name == "bad":
            raise MCPError(INVALID_PARAMS, "bad arguments")
        if name == "tool_error":
            return Result("the tool failed", is_error=True)
        raise MCPError(INVALID_PARAMS, f"unknown tool {name}")

    async def watch(self) -> None:
        await self.broken.wait()
        raise ConnectionResetError("connection reset by peer")


@dataclass(eq=False)
class FakeServer:
    """Counts connections; refuses them while `down` is set."""

    down: bool = False
    conns: list[FakeConn] = field(default_factory=list)
    attempts: list[float] = field(default_factory=list)
    seen: set = field(default_factory=set)
    active: int = 0
    max_active: int = 0

    @asynccontextmanager
    async def connect(self):
        self.attempts.append(time.monotonic())
        if self.down:
            raise ConnectionRefusedError("connection refused")
        conn = FakeConn(self, len(self.conns))
        self.conns.append(conn)
        async with anyio.create_task_group() as tg:
            tg.start_soon(conn.watch)
            try:
                yield conn
            finally:
                conn.closed = True
                tg.cancel_scope.cancel()

    def live(self) -> list[FakeConn]:
        return [c for c in self.conns if not c.closed]
