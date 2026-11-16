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
        self.retry_flags = []

    async def start(self, wait=1, timeout=None, *, all_servers=False):
        pass

    async def call(self, tool, args, *, timeout=None, idempotent=False):
        self.calls.append((tool, args))
        self.retry_flags.append(idempotent)
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


async def test_only_explicitly_idempotent_calls_may_be_retried(service):
    client = ToolServiceClient(service.requests, service.replies[0], 0)
    client.start()
    try:
        await client.call("write", "change", {"key": "x"})
        await client.call("read", "lookup", {"key": "x"}, idempotent=True)
        assert service.pool.retry_flags == [False, True]
    finally:
        client.stop()
        service.stop()


async def test_client_stop_settles_unanswered_calls():
    client = ToolServiceClient(queue.Queue(), queue.Queue(), 0)
    client.start()
    task = asyncio.create_task(client.call("uid", "tool", {}))
    await asyncio.sleep(0)
    client.stop()
    with pytest.raises(ToolServiceError, match="stopped"):
        await asyncio.wait_for(task, 1)
    with pytest.raises(RuntimeError, match="running"):
        await client.call("again", "tool", {})
    assert client.stats()["unanswered"] == 0


async def test_serialization_failure_reaches_caller():
    client = ToolServiceClient(queue.Queue(), queue.Queue(), 0)
    client.start()
    try:
        with pytest.raises(ToolServiceError, match="could not send"):
            await asyncio.wait_for(client.call("uid", "tool", {"bad": lambda: 1}), 1)
        assert not client.pending
    finally:
        client.stop()


async def test_ready_failure_closes_pool_and_releases_clients():
    class Pool(FakePool):
        closed = False

        async def close(self, timeout=10):
            self.closed = True

    pool = Pool()
    replies = queue.Queue()
    def fail(_):
        f = asyncio.get_running_loop().create_future()
        f.set_exception(ValueError("ready failed"))
        return f

    with pytest.raises(ValueError, match="ready failed"):
        await asyncio.wait_for(serve(queue.Queue(), [replies], pool, on_ready=fail), 1)
    assert pool.closed and pickle.loads(replies.get_nowait()) is None


async def test_listener_failures_still_deliver_answers():
    def fail(*args):
        raise ValueError("listener failed")

    requests, replies = queue.Queue(), queue.Queue()
    client = ToolServiceClient(requests, replies, 0, listener=fail)
    server = asyncio.create_task(serve(requests, [replies], FakePool(), listener=fail))
    client.start()
    try:
        assert await asyncio.wait_for(client.call("uid", "lookup", {"key": 7}), 1) == {"echo": 7}
    finally:
        requests.put(None)
        await asyncio.wait_for(server, 1)
        client.stop()


async def test_real_sdk_result_through_supervised_service():
    from mcp import Client
    from mcp.server.mcpserver import MCPServer
    from flowgentic.tools.mcp import SupervisedMcpPool

    mcp = MCPServer("local")

    @mcp.tool()
    def echo(value: str) -> str:
        return value

    pool = SupervisedMcpPool(lambda: Client(mcp), clients=1, per_client=1)
    requests, replies = queue.Queue(), queue.Queue()
    client = ToolServiceClient(requests, replies, 0)
    service = asyncio.create_task(serve(requests, [replies], pool))
    client.start()
    try:
        good, bad = await asyncio.wait_for(asyncio.gather(
            client.call("ok", "echo", {"value": "hello"}),
            client.call("bad", "echo", {"value": lambda: None}),
            return_exceptions=True,
        ), 5)
        assert good == "hello" and isinstance(bad, ToolServiceError)
    finally:
        requests.put(None)
        await asyncio.wait_for(service, 2)
        client.stop()


async def test_shelf_replays_a_completed_uid_after_reopening(tmp_path):
    import shelve
    from flowgentic.tools.service import ToolService

    pool = FakePool()
    replies = []
    def out(src, items):
        replies.extend(items)
    request = [("a", "lookup", {"key": 42}, 0, 0)]
    for i in range(2):
        with shelve.open(str(tmp_path / "answers")) as answers:
            service = ToolService(pool, out, answer_store=answers)
            service.on_requests(request, 0)
            await asyncio.gather(*service._tasks)
            service.flush()
            assert service.stats()["repeats_done"] == i
            assert not service.calls
    assert len(pool.calls) == 1
    assert [r[2] for r in replies] == [{"echo": 42}] * 2


async def test_answer_store_failure_never_reissues_an_uncertain_call():
    from flowgentic.tools.service import ToolService

    class Failing(dict):
        def get(self, key):
            if key == "unknown":
                raise OSError("cannot read")
            return super().get(key)

        def __setitem__(self, key, value):
            raise OSError("cannot write")

    replies = []
    pool = FakePool()
    service = ToolService(pool, lambda src, items: replies.extend(items), answer_store=Failing())
    for uid in ("unknown", "a", "a"):
        service.on_requests([(uid, "lookup", {"key": 42}, 0, 0)], 0)
        await asyncio.gather(*service._tasks)
        service.flush()
    assert len(pool.calls) == 1 and [r[1] for r in replies] == [False, True, True]
