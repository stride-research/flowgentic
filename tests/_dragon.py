"""Helpers for the Dragon suite.

Tests marked ``dragon`` run the parts on a real Dragon runtime: RaDEX on a
Dragon distributed dictionary made by RHAPSODY's ``DragonDataBackend``, Dragon
processes and queues, and RHAPSODY's native Dragon backend. They need Dragon,
RaDEX's Python client and a RHAPSODY with that backend, which no package
index has, so CI skips them. Run them by hand, under ``dragon``::

    dragon tests/dragon_pytest.py -m dragon tests

Everything Dragon is imported inside the helpers, so the test modules import
without it.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

GiB = 1024**3


def under_dragon() -> bool:
    """Whether this process runs in a Dragon runtime, started by ``dragon``."""
    if not os.environ.get("DRAGON_MY_PUID"):
        return False
    try:
        import dragon  # noqa: F401
    except ImportError:
        return False
    return True


@contextmanager
def dictionary(managers: int = 2, manager_gib: float = 1.0) -> Iterator[str]:
    """A Dragon distributed dictionary from RHAPSODY's ``DragonDataBackend``,
    on this node; yields its descriptor, for ``RadexStore.attach``."""
    from rhapsody.backends.data.dragon import DragonDataBackend

    data = DragonDataBackend(
        name="flowgentic-tests",
        managers_per_node=managers,
        n_nodes=1,
        total_mem=int(managers * manager_gib * GiB),
    )
    asyncio.run(data.start())
    try:
        yield data.endpoints[0].serialize()
    finally:
        asyncio.run(data.shutdown())


def clear(descriptor: str) -> None:
    """Remove every key from the dictionary, between tests."""
    from dragon.data.ddict import DDict

    d = DDict.attach(descriptor)
    try:
        d.clear()
    finally:
        d.detach()


def dragon_queue(maxsize: int = 1024) -> Any:
    """A Dragon queue: ``put``, ``get(timeout=...)``, ``get(block=False)``."""
    from dragon.native.queue import Queue

    return Queue(maxsize=maxsize, block_size=4096)


def start(target: Callable[..., Any], *args: Any) -> Any:
    """Start ``target(*args)`` in a Dragon process of its own."""
    from dragon.native.process import Process

    p = Process(target, args=args)
    p.start()
    return p


def join(*procs: Any, timeout: float = 120.0) -> None:
    """Wait for Dragon processes; fail on a timeout or a non-zero exit."""
    for p in procs:
        p.join(timeout=timeout)
        if p.returncode is None:
            p.terminate()
            raise TimeoutError(f"Dragon process {p.puid} did not end in {timeout} s")
        if p.returncode != 0:
            raise RuntimeError(f"Dragon process {p.puid} exited with {p.returncode}")


def take(q: Any, n: int, timeout: float = 120.0) -> list:
    """``n`` items from a queue, waiting at most ``timeout`` in all."""
    deadline = time.monotonic() + timeout
    return [q.get(timeout=max(0.1, deadline - time.monotonic())) for _ in range(n)]


# --- a live MCP server, in a process of its own, on 127.0.0.1 ----------------


def serve_mcp(port: int) -> None:
    """An MCP server with ``echo`` and ``fail`` on 127.0.0.1:``port``."""
    import uvicorn
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    server = MCPServer("dragon-suite", log_level="WARNING")

    @server.tool()
    async def echo(key: str) -> dict:
        """Answer with the key, after a short wait."""
        await asyncio.sleep(0.005)
        return {"echo": key, "pid": os.getpid()}

    @server.tool()
    def fail(key: str) -> str:
        """Always fails, as a tool reports its own error."""
        raise ToolError(f"no such key: {key}")

    app = server.streamable_http_app(
        stateless_http=True, json_response=True, host="127.0.0.1"
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


@contextmanager
def mcp_server() -> Iterator[str]:
    """``serve_mcp`` in a subprocess; yields its URL."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, __file__, "--mcp-server", str(port)])
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("the MCP server did not start") from None
                time.sleep(0.05)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


if __name__ == "__main__" and sys.argv[1:2] == ["--mcp-server"]:
    serve_mcp(int(sys.argv[2]))
