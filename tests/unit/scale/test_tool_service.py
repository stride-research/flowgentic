"""The tool service in its own thread and event loop, two agent-process clients,
stdlib queues: repeated uids, errors, batching, stopping."""

import asyncio
import pickle
import queue
import threading
from types import SimpleNamespace

import pytest

from flowgentic.tools.service import CHUNK, ToolServiceClient, ToolServiceError, serve


class FakePool:
    servers = ["fake"]

    def __init__(self):
        self.calls = []

    async def start(self, wait=1, timeout=None):
        pass

    async def call(self, tool, args, *, timeout=None, idempotent=False):
        self.calls.append((tool, args))
        await asyncio.sleep(args.get("sleep", 0))
        if tool == "bad":
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(text="no such key")])
        return SimpleNamespace(is_error=False, structured_content={"echo": args["key"]}, content=[])

    def stats(self):
        return {"calls": len(self.calls)}

    async def close(self, timeout=10.0):
        pass


@pytest.fixture
def service():
    requests, replies = queue.Queue(), [queue.Queue(), queue.Queue()]
    pool = FakePool()
    out = {}
    ready = threading.Event()
    calls = []

    def run():
        out["stats"] = asyncio.run(
            serve(
                requests,
                replies,
                pool,
                on_ready=lambda state: ready.set(),
                listener=lambda *call: calls.append(call),
            )
        )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(5)

    def stop():
        requests.put(pickle.dumps(None))
        thread.join(5)
        return out["stats"]

    yield SimpleNamespace(requests=requests, replies=replies, pool=pool, stop=stop, calls=calls)


async def test_calls_repeats_errors_and_stop(service):
    times = []
    a = ToolServiceClient(service.requests, service.replies[0], 0, listener=lambda *t: times.append(t))
    b = ToolServiceClient(service.requests, service.replies[1], 1)
    a.start()
    b.start()
    try:
        first, again, other = await asyncio.gather(
            a.call("agent-1/c1", "lookup", {"key": "x", "sleep": 0.1}),
            b.call("agent-1/c1", "lookup", {"key": "x", "sleep": 0.1}),  # joins the running call
            b.call("agent-2/c1", "lookup", {"key": "y"}),
        )
        assert first == again == {"echo": "x"} and other == {"echo": "y"}
        assert await a.call("agent-1/c1", "lookup", {"key": "x"}) == {"echo": "x"}  # kept answer
        with pytest.raises(ToolServiceError, match="no such key"):
            await a.call("agent-3/c1", "bad", {"key": "z"})
    finally:
        a.stop()
        b.stop()
    stats = service.stop()
    assert len(service.pool.calls) == 3  # x once, y, bad
    assert stats["repeats_running"] == 1 and stats["repeats_done"] == 1
    assert stats["ok"] == {"lookup": 2} and stats["failed"] == {"bad": 1}
    assert len(service.calls) == 3
    assert len(times) == 3 and all(total >= at_service >= 0 for _, total, at_service in times)


async def test_a_burst_goes_out_in_batches(service):
    client = ToolServiceClient(service.requests, service.replies[0], 0)
    client.start()
    try:
        n = 2 * CHUNK + 10
        out = await asyncio.gather(
            *(client.call(f"agent-{i}/c", "lookup", {"key": str(i)}) for i in range(n))
        )
    finally:
        client.stop()
    assert out[-1] == {"echo": str(n - 1)}
    assert client.stats()["request_messages"] == 3
    assert service.stop()["unique"] == n


async def test_a_client_must_be_started():
    client = ToolServiceClient(queue.Queue(), queue.Queue(), 0)
    with pytest.raises(RuntimeError, match="start"):
        await client.call("u", "t", {})
