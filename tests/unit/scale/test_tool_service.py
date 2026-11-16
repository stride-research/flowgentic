"""The tool service in its own thread and event loop, two agent-process clients,
stdlib queues: repeated uids, errors, batching, stopping."""

import asyncio
import pickle
import queue
import threading
import time
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
    with pytest.raises(ToolServiceError, match="tool service stopped"):
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
    failed = []

    @mcp.tool()
    def echo(value: str) -> str:
        return value

    @mcp.tool()
    def fail(key: str) -> str:
        failed.append(key)
        raise ValueError(f"no such key: {key}")

    pool = SupervisedMcpPool(lambda: Client(mcp), clients=1, per_client=1)
    requests, replies = queue.Queue(), queue.Queue()
    client = ToolServiceClient(requests, replies, 0)
    service = asyncio.create_task(serve(requests, [replies], pool))
    client.start()
    try:
        good, bad = await asyncio.wait_for(asyncio.gather(
            client.call("ok", "echo", {"value": "hello"}),
            client.call("bad", "fail", {"key": "x"}),
            return_exceptions=True,
        ), 5)
        assert good == "hello"
        assert isinstance(bad, ToolServiceError) and "fail" in str(bad)
        # The tool's own error is final: asking again replays it.
        with pytest.raises(ToolServiceError, match="fail"):
            await asyncio.wait_for(client.call("bad", "fail", {"key": "x"}), 5)
        assert failed == ["x"]
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


async def until(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "condition not met in time"
        await asyncio.sleep(0.005)


async def ask(service, uid, tool, args, idempotent=False):
    """One request straight to a ToolService; its answers as (ok, payload)."""
    replies = []
    service.out = lambda src, items: replies.extend(items)
    service.on_requests([(uid, tool, args, 0, 0, idempotent)], 0)
    await asyncio.gather(*service._tasks)
    service.flush()
    return [(r[1], r[2]) for r in replies]


@pytest.mark.parametrize("store", ["memory", "shelf"])
async def test_a_call_that_never_ran_is_not_kept(store, tmp_path):
    import shelve
    from flowgentic.tools.mcp import McpUnavailable
    from flowgentic.tools.service import ToolService

    class Pool(FakePool):
        async def call(self, tool, args, **kw):
            if not self.calls:
                self.calls.append((tool, args))
                raise McpUnavailable("no MCP client connected for 10 s")
            return await super().call(tool, args, **kw)

    pool = Pool()
    answers = shelve.open(str(tmp_path / "answers")) if store == "shelf" else None
    service = ToolService(pool, None, answer_store=answers)
    try:
        assert await ask(service, "a", "lookup", {"key": 1}) == [
            (False, "McpUnavailable: no MCP client connected for 10 s")
        ]
        assert not service.calls and (answers is None or "a" not in answers)
        assert await ask(service, "a", "lookup", {"key": 1}) == [(True, {"echo": 1})]
        assert await ask(service, "a", "lookup", {"key": 1}) == [(True, {"echo": 1})]
        assert len(pool.calls) == 2 and service.stats()["retryable"] == 1
    finally:
        if answers is not None:
            answers.close()
    if store == "shelf":
        with shelve.open(str(tmp_path / "answers")) as answers:
            assert dict(answers) == {"a": (True, {"echo": 1})}


@pytest.mark.parametrize("idempotent, runs", [(False, 1), (True, 2)])
async def test_a_call_that_may_have_run_is_kept_unless_idempotent(idempotent, runs):
    from mcp_fakes import FakeServer
    from flowgentic.tools.mcp import Backoff, SupervisedMcpPool
    from flowgentic.tools.service import ToolService

    srv = FakeServer()
    fast = Backoff(initial=0.01, factor=2.0, maximum=0.05, jitter=0.0)
    async with SupervisedMcpPool(srv.connect, clients=1, per_client=1, backoff=fast, resends=0) as pool:
        service = ToolService(pool, None)
        for _ in range(2):
            [(ok, payload)] = await ask(service, "w", "break", {}, idempotent)
            assert not ok and payload.startswith("McpConnectionLost")
        assert sum(c.names.count("break") for c in srv.conns) == runs
        assert ("w" in service.calls) is not idempotent


async def test_shutdown_keeps_no_call_that_never_ran():
    from mcp_fakes import FakeServer
    from flowgentic.tools.mcp import SupervisedMcpPool

    srv = FakeServer()
    pool = SupervisedMcpPool(srv.connect, clients=1, per_client=2)
    store = {}
    requests, replies = queue.Queue(), queue.Queue()
    service = asyncio.create_task(
        serve(requests, [replies], pool, drain_timeout=0.1, answer_store=store)
    )
    await until(lambda: pool.stats()["connected"] == 1)
    client = ToolServiceClient(requests, replies, 0)
    client.start()
    calls = [  # the first two run on the client's two slots; the rest wait in the queue
        asyncio.create_task(client.call("running", "hang", {})),
        asyncio.create_task(client.call("running-idempotent", "hang", {}, idempotent=True)),
        asyncio.create_task(client.call("queued", "hang", {})),
        asyncio.create_task(client.call("queued-idempotent", "hang", {}, idempotent=True)),
    ]
    await until(lambda: srv.active == 2)
    requests.put(pickle.dumps(None))
    stats = await asyncio.wait_for(service, 5)
    out = await asyncio.wait_for(asyncio.gather(*calls, return_exceptions=True), 2)
    assert all(isinstance(r, ToolServiceError) for r in out)
    assert ["in flight" in str(r) for r in out] == [True, True, False, False]
    assert list(store) == ["running"]  # the only one that may have run and must not run twice
    assert stats["retryable"] == 3


class _Unloadable:
    """Pickles, but cannot be unpickled: like a class only the agent process can import."""

    def __reduce__(self):
        return (getattr, (int, "no_such_attribute"))


async def test_a_message_that_cannot_be_unpickled_is_skipped():
    requests, replies = queue.Queue(), queue.Queue()
    service = asyncio.create_task(serve(requests, [replies], FakePool()))
    client = ToolServiceClient(requests, replies, 0)
    client.start()
    try:
        requests.put(pickle.dumps([("lost", "lookup", {"x": _Unloadable()}, 0, 0)]))
        assert await asyncio.wait_for(client.call("next", "lookup", {"key": 1}), 2) == {"echo": 1}
    finally:
        requests.put(pickle.dumps(None))
        stats = await asyncio.wait_for(service, 2)
        client.stop()
    assert stats["skipped_messages"] == 1 and stats["unique"] == 1


async def test_a_failed_reader_stops_the_service_and_settles_callers():
    class Pool(FakePool):
        async def call(self, tool, args, **kw):
            await asyncio.sleep(3600)

    requests, replies = queue.Queue(), queue.Queue()
    service = asyncio.create_task(serve(requests, [replies], Pool(), drain_timeout=0.1))
    client = ToolServiceClient(requests, replies, 0)
    client.start()
    waiting = asyncio.create_task(client.call("waiting", "lookup", {}))
    await until(lambda: requests.empty())
    await asyncio.sleep(0.05)
    requests.put(pickle.dumps(5))  # unpickles, but is not a list of requests
    stats = await asyncio.wait_for(service, 2)
    with pytest.raises(ToolServiceError, match="stopped"):
        await asyncio.wait_for(waiting, 2)
    assert stats["failed"] == {"lookup": 1}
