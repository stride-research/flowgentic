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


# --- a service that is gone ----------------------------------------------------------


async def test_a_call_with_no_answer_fails_at_its_deadline():
    """Nobody answers, as when the service's process was killed: the call
    fails at its deadline, and an answer that comes later is dropped."""
    requests, replies = queue.Queue(), queue.Queue()
    client = ToolServiceClient(requests, replies, 0, timeout=0.2)
    client.start()
    try:
        t = time.monotonic()
        with pytest.raises(ToolServiceError, match="no answer from the tool service within 0.2 s"):
            await asyncio.wait_for(client.call("a/c1", "lookup", {"key": 1}), 2)
        assert time.monotonic() - t < 1
        assert client.stats()["unanswered"] == 0 and client.stats()["expired"] == 1
        replies.put(pickle.dumps([("a/c1", True, {"late": 1}, (0, 0, 0))]))
        await asyncio.sleep(0.05)
        assert client.stats()["answered"] == 0
    finally:
        client.stop()


def test_a_deadline_must_be_positive():
    with pytest.raises(ValueError, match="timeout"):
        ToolServiceClient(queue.Queue(), queue.Queue(), 0, timeout=0)


async def test_a_failed_liveness_check_fails_every_waiting_call():
    requests, replies = queue.Queue(), queue.Queue()
    alive = [True]
    client = ToolServiceClient(requests, replies, 0, alive=lambda: alive[0])
    client.start()
    calls = [asyncio.create_task(client.call(f"a/c{i}", "lookup", {"key": i})) for i in range(3)]
    await asyncio.sleep(0.05)
    alive[0] = False
    out = await asyncio.wait_for(asyncio.gather(*calls, return_exceptions=True), 3)
    assert all(isinstance(e, ToolServiceError) and "not running" in str(e) for e in out)
    with pytest.raises(ToolServiceError, match="stopped"):
        await client.call("a/c9", "lookup", {"key": 9})


def running(pid: int) -> bool:
    """Whether process ``pid`` exists: the check an agent process can make of
    a service on its node, since a ``Process``'s ``is_alive`` works only in the
    process that started it."""
    import os

    try:
        os.kill(pid, 0)  # signal 0 only checks that the process exists
    except ProcessLookupError:
        return False
    return True


async def test_a_killed_service_fails_waiting_calls():
    """The service's process is killed, as by the out-of-memory killer, with a
    call in flight from each of two agent processes: one client tests the
    service's pid, the other has only its deadline. Neither waits forever."""
    import multiprocessing
    import os
    import signal

    from service_process import serve_hanging

    ctx = multiprocessing.get_context("spawn")
    requests, replies = ctx.Queue(), [ctx.Queue(), ctx.Queue()]
    proc = ctx.Process(target=serve_hanging, args=(requests, replies), daemon=True)
    proc.start()
    pid = proc.pid
    watched = ToolServiceClient(requests, replies[0], 0, alive=lambda: running(pid))
    deadline = ToolServiceClient(requests, replies[1], 1, timeout=4.0)
    watched.start()
    deadline.start()
    try:
        first = asyncio.create_task(watched.call("a/c1", "lookup", {"key": 1}))
        second = asyncio.create_task(deadline.call("b/c1", "lookup", {"key": 2}))
        await asyncio.sleep(1.0)
        assert running(pid) and not first.done()
        os.kill(pid, signal.SIGKILL)
        proc.join(5)  # reaped by its parent: until then, its pid still exists
        t = time.monotonic()
        with pytest.raises(ToolServiceError, match="not running"):
            await asyncio.wait_for(first, 3)
        assert time.monotonic() - t < 2.5
        with pytest.raises(ToolServiceError, match="no answer"):
            await asyncio.wait_for(second, 5)
    finally:
        watched.stop()
        deadline.stop()


async def test_a_failing_liveness_check_is_logged_once_and_ignored(caplog, monkeypatch):
    """A check that cannot run where the client is, such as ``is_alive`` of a
    ``Process`` the agent process did not start, raises every time it is asked.
    The client warns once, with the traceback, and keeps waiting for its answer;
    later failures go to DEBUG, not a warning a second."""
    import logging

    from flowgentic.tools import service as service_module

    monkeypatch.setattr(service_module._QueueReader, "poll_s", 0.02)  # a check every 20 ms
    caplog.set_level(logging.DEBUG, logger="flowgentic.tools.service")
    asked = []

    def broken():
        asked.append(1)
        raise AssertionError("can only test a child process")

    requests, replies = queue.Queue(), queue.Queue()
    client = ToolServiceClient(requests, replies, 0, alive=broken)
    client.start()
    try:
        call = asyncio.create_task(client.call("a/c1", "lookup", {"key": 1}))
        await until(lambda: len(asked) >= 10)
        replies.put(pickle.dumps([("a/c1", True, {"echo": 1}, (0, 0, 0))]))
        assert await asyncio.wait_for(call, 2) == {"echo": 1}
    finally:
        client.stop()
    failed = [r for r in caplog.records if "liveness check failed" in r.getMessage()]
    assert [r.levelno for r in failed if r.levelno >= logging.WARNING] == [logging.WARNING]
    assert failed[0].levelno == logging.WARNING and failed[0].exc_info is not None
    assert len(failed) >= 10 and all(r.levelno == logging.DEBUG for r in failed[1:])


# --- arguments travel as JSON ---------------------------------------------------------


async def test_arguments_travel_as_json_so_no_batch_is_lost():
    """A request whose arguments hold an object of a class that only the agent
    processes import goes out as JSON, so the service reads it and the other
    requests sent with it."""
    import enum
    import sys
    import types

    module = types.ModuleType("agent_only_schemas")
    exec(
        "import enum\nfrom pydantic import BaseModel\n"
        "class Range(BaseModel):\n    lo: float\n    hi: float\n"
        "class Unit(str, enum.Enum):\n    K = 'K'\n",
        module.__dict__,
    )
    sys.modules["agent_only_schemas"] = module
    requests, replies = queue.Queue(), queue.Queue()
    client = ToolServiceClient(requests, replies, 0)
    client.start()
    pool = FakePool()
    try:
        calls = [asyncio.create_task(client.call(f"agent-{i}/c1", "lookup", {"key": i})) for i in range(5)]
        args = {"key": "x", "t": module.Range(lo=1, hi=2), "unit": module.Unit.K}
        calls.append(asyncio.create_task(client.call("agent-9/c1", "lookup", args)))
        await asyncio.sleep(0.05)  # one loop pass: all six went out in one message
        assert requests.qsize() == 1
    finally:
        del sys.modules["agent_only_schemas"]  # the service process does not have it
    service = asyncio.create_task(serve(requests, [replies], pool))
    try:
        done, _ = await asyncio.wait(calls, timeout=3)
        assert len(done) == 6
        assert pool.calls[-1] == ("lookup", {"key": "x", "t": {"lo": 1.0, "hi": 2.0}, "unit": "K"})
        assert type(pool.calls[-1][1]["unit"]) is str
        # Arguments that have no JSON form fail their own call, at once.
        with pytest.raises(ToolServiceError, match="not JSON"):
            await asyncio.wait_for(client.call("agent-10/c1", "lookup", {"key": enum}), 1)
        assert not client.pending
    finally:
        requests.put(None)
        await asyncio.wait_for(service, 2)
        client.stop()


# --- one reply that cannot be delivered ------------------------------------------------


async def test_a_request_from_an_unknown_process_is_refused_on_arrival():
    """A client whose index has no reply queue, here one past the end, gets no
    tool call made for it, and the other clients' answers still go out."""
    requests, replies = queue.Queue(), [queue.Queue(), queue.Queue()]
    pool = FakePool()
    service = asyncio.create_task(serve(requests, replies, pool))
    stray = ToolServiceClient(requests, queue.Queue(), index=2)
    good = ToolServiceClient(requests, replies[0], index=0)
    stray.start()
    good.start()
    try:
        lost = asyncio.create_task(stray.call("x/1", "lookup", {"key": "b"}))
        assert await asyncio.wait_for(good.call("y/1", "lookup", {"key": "g"}), 2) == {"echo": "g"}
        assert not lost.done()
        stray.stop()
        with pytest.raises(ToolServiceError, match="stopped"):
            await lost
    finally:
        requests.put(None)
        stats = await asyncio.wait_for(service, 2)
        stray.stop()
        good.stop()
    assert pool.calls == [("lookup", {"key": "g"})]
    assert stats["unknown_sources"] == 1


async def test_an_answer_that_cannot_be_delivered_loses_no_other():
    from flowgentic.tools.service import ToolService

    delivered = {}

    def out(src, items):
        if src == 0:
            raise OSError("reply queue closed")
        delivered[src] = items

    service = ToolService(FakePool(), out)
    service.on_requests(
        [(f"agent-{src}/c1", "lookup", {"key": src}, src, 0) for src in range(3)], 0
    )
    await asyncio.gather(*service._tasks)
    service.flush()
    assert sorted(delivered) == [1, 2]
    assert service.stats()["undelivered"] == 1


async def test_one_unwritable_reply_queue_does_not_stop_the_others():
    class Unwritable(queue.Queue):
        def put(self, item, block=True, timeout=None):
            raise OSError("reply queue closed")

    requests, replies = queue.Queue(), [Unwritable(), queue.Queue()]
    service = asyncio.create_task(serve(requests, replies, FakePool()))
    gone = ToolServiceClient(requests, queue.Queue(), index=0)
    good = ToolServiceClient(requests, replies[1], index=1)
    gone.start()
    good.start()
    try:
        lost = asyncio.create_task(gone.call("x/1", "lookup", {"key": "b"}))
        assert await asyncio.wait_for(good.call("y/1", "lookup", {"key": "g"}), 2) == {"echo": "g"}
        gone.stop()
        with pytest.raises(ToolServiceError):
            await lost
        requests.put(None)
        stats = await asyncio.wait_for(service, 2)
        await until(lambda: good._stopped)  # the stop still reached the good client
    finally:
        gone.stop()
        good.stop()
    assert stats["undelivered"] == 1


# --- what an MCP result carries ------------------------------------------------------


async def test_mcp_data_reads_every_result_shape_of_the_sdk():
    from mcp import Client
    from mcp.server.mcpserver import MCPServer

    from flowgentic.tools.service import mcp_data

    mcp = MCPServer("local")

    @mcp.tool()
    def returns_none(key: str) -> None:
        return None

    @mcp.tool()
    def returns_nothing_unannotated(key: str):
        return None

    @mcp.tool()
    def returns_text(key: str) -> str:
        return "00123"

    @mcp.tool()
    def returns_list_unannotated(key: str):
        return ["a", '{"b": 1}', "c"]

    @mcp.tool()
    def returns_list(key: str) -> list[str]:
        return ["a", "b"]

    @mcp.tool()
    def fails(key: str) -> str:
        raise ValueError("no such key")

    async with Client(mcp) as client:
        results = {
            name: mcp_data(await client.call_tool(name, {"key": "k"}))
            for name in [
                "returns_none",
                "returns_nothing_unannotated",
                "returns_text",
                "returns_list_unannotated",
                "returns_list",
                "fails",
            ]
        }
    assert results["returns_none"] == (True, None)
    assert results["returns_nothing_unannotated"] == (True, None)  # success, with no data
    assert results["returns_text"] == (True, "00123")
    assert results["returns_list_unannotated"] == (True, [{"text": "a"}, {"b": 1}, {"text": "c"}])
    assert results["returns_list"] == (True, ["a", "b"])
    ok, message = results["fails"]
    assert not ok and "Error executing tool fails" in message


def test_mcp_data_without_structured_content():
    from flowgentic.tools.service import mcp_data

    def text(t):
        return SimpleNamespace(type="text", text=t)

    def result(*blocks, is_error=False):
        return SimpleNamespace(is_error=is_error, content=list(blocks), structured_content=None)

    assert mcp_data(result()) == (True, None)
    assert mcp_data(result(text(""))) == (True, None)
    assert mcp_data(result(text('{"y": 2}'))) == (True, {"y": 2})
    assert mcp_data(result(text("plain"))) == (True, {"text": "plain"})
    assert mcp_data(result(text("1"), text("two"))) == (True, [1, {"text": "two"}])
    assert mcp_data(result(text("bad"), text("worse"), is_error=True)) == (False, "bad worse")
    image = SimpleNamespace(type="image", data="...", mime_type="image/png")
    ok, message = mcp_data(result(image))
    assert not ok and "no text" in message
