"""The supervised MCP pool against fake clients built like mcp 2.3.0's (see mcp_fakes.py). Fast: no network."""

import asyncio
import random
import time

import pytest
from mcp_fakes import FakeServer
from mcp import MCPError

from flowgentic.tools.mcp import Backoff, McpConnectionLost, McpPoolClosed, McpUnavailable, SupervisedMcpPool


FAST = Backoff(initial=0.01, factor=2.0, maximum=0.05, jitter=0.0)


async def until(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "condition not met in time"
        await asyncio.sleep(0.005)


async def test_the_fake_fails_like_the_sdk():
    """Without the pool, a broken connection cancels the task that entered the client."""
    srv = FakeServer()
    seen = []
    with pytest.raises(BaseExceptionGroup) as info:
        async with srv.connect() as conn:
            other = asyncio.create_task(conn.call_tool("slow", {"s": 1.0}))
            breaker = asyncio.create_task(conn.call_tool("break", {}))
            try:
                await asyncio.sleep(1.0)  # the entering task's own work
            except asyncio.CancelledError:
                seen.append("own work cancelled")
                raise
            finally:
                await asyncio.gather(other, breaker, return_exceptions=True)
    assert seen == ["own work cancelled"]
    assert isinstance(info.value.exceptions[0], ConnectionResetError)


async def test_a_broken_connection_fails_only_its_own_calls():
    srv = FakeServer()
    async with SupervisedMcpPool(srv.connect, clients=2, per_client=2, backoff=FAST) as pool:
        await until(lambda: len(srv.live()) == 2)
        slow = [asyncio.create_task(pool.call("slow", {"s": 0.3})) for _ in range(3)]
        await until(lambda: srv.active == 3)
        brk = asyncio.create_task(pool.call("break", {"after": 0.05}))
        queued = [asyncio.create_task(pool.call("echo", {"i": i})) for i in range(5)]
        await asyncio.sleep(0.5)  # the caller's own work goes on
        assert asyncio.current_task().cancelling() == 0
        out = await asyncio.gather(*slow, brk, *queued, return_exceptions=True)

        broken = next(c for c in srv.conns if "break" in c.names)
        lost = [r for r in out if isinstance(r, McpConnectionLost)]
        # The break call and the one slow call on the same client fail; nothing else does.
        assert len(lost) == 2 and broken.names.count("slow") == 1
        assert isinstance(out[3], McpConnectionLost)
        assert [r.content for r in out[4:]] == [{"i": i} for i in range(5)]
        assert sum(not isinstance(r, Exception) for r in out[:3]) == 2
        assert pool.counts["restarts"] == 1 and pool.counts["connection_lost"] == 2
        await until(lambda: pool.stats()["connected"] == 2)
        assert len(srv.conns) == 3
        assert (await pool.call("echo", {"x": 1})).content == {"x": 1}


async def test_reconnects_with_exponential_backoff_and_resets_after_a_good_connection():
    srv = FakeServer(down=True)
    backoff = Backoff(initial=0.02, factor=2.0, maximum=0.1, jitter=0.0)
    pool = SupervisedMcpPool(srv.connect, clients=1, per_client=1, backoff=backoff)
    await pool.start(wait=0)
    await until(lambda: len(srv.attempts) >= 6)
    srv.down = False
    await until(lambda: pool.stats()["connected"] == 1)
    gaps = [b - a for a, b in zip(srv.attempts, srv.attempts[1:])][:5]
    for gap, want in zip(gaps, [0.02, 0.04, 0.08, 0.1, 0.1]):
        assert want * 0.9 <= gap <= want + 0.05, (gaps, want)
    assert pool.counts["connect_failures"] >= 6

    assert (await pool.call("echo", {})).content == {}  # this connection served a call
    n = len(srv.attempts)
    t = time.monotonic()
    srv.live()[0].broken.set()
    await until(lambda: len(srv.attempts) > n)
    assert time.monotonic() - t < 0.02 + 0.05  # back to the initial delay, not 0.1
    await pool.close()


def test_backoff_jitter_stays_in_range():
    b = Backoff(initial=1.0, factor=2.0, maximum=8.0, jitter=0.5)
    rng = random.Random(7)
    for attempt in range(10):
        base = min(8.0, 2.0**attempt)
        for _ in range(50):
            assert base * 0.5 <= b.delay(attempt, rng) <= base


async def test_calls_are_bounded_per_client():
    srv = FakeServer()
    async with SupervisedMcpPool(srv.connect, clients=2, per_client=3, backoff=FAST) as pool:
        await until(lambda: len(srv.live()) == 2)
        out = await asyncio.gather(*(pool.call("slow", {"s": 0.03}) for _ in range(30)))
        assert len(out) == 30
        assert all(c.max_active <= 3 for c in srv.conns)
        assert srv.max_active == 6


async def test_close_fails_what_is_left_and_never_raises():
    srv = FakeServer()
    pool = SupervisedMcpPool(srv.connect, clients=2, per_client=2, backoff=FAST)
    await pool.start(wait=2)
    calls = [asyncio.create_task(pool.call("hang", {})) for _ in range(7)]
    await until(lambda: srv.active == 4)
    t = time.monotonic()
    await pool.close(timeout=2.0)
    assert time.monotonic() - t < 0.5
    out = await asyncio.gather(*calls, return_exceptions=True)
    assert all(isinstance(r, McpPoolClosed) for r in out)
    assert all(c.closed for c in srv.conns) and sum(c.cancelled for c in srv.conns) == 4
    assert all(t.done() for t in pool._supervisors)
    with pytest.raises(McpPoolClosed):
        await pool.call("echo", {})
    await pool.close()  # a second close returns at once


async def test_close_can_drain():
    srv = FakeServer()
    pool = SupervisedMcpPool(srv.connect, clients=1, per_client=2, backoff=FAST)
    await pool.start()
    calls = [asyncio.create_task(pool.call("slow", {"s": 0.1})) for _ in range(4)]
    await until(lambda: srv.active == 2)
    await pool.close(drain=1.0)
    assert [r.content for r in await asyncio.gather(*calls)] == [{"s": 0.1}] * 4


async def test_fails_fast_while_the_server_is_unreachable_and_recovers():
    srv = FakeServer(down=True)
    pool = SupervisedMcpPool(srv.connect, clients=2, per_client=1, backoff=FAST, unavailable_after=0.2)
    await pool.start(wait=0)
    t = time.monotonic()
    with pytest.raises(McpUnavailable):
        await pool.call("echo", {})  # queued, then failed when the pool gives up
    assert 0.15 <= time.monotonic() - t <= 0.5
    t = time.monotonic()
    with pytest.raises(McpUnavailable):
        await pool.call("echo", {})  # at once
    assert time.monotonic() - t < 0.01
    srv.down = False
    await until(lambda: pool.stats()["connected"] >= 1)
    assert (await pool.call("echo", {"back": True})).content == {"back": True}
    await pool.close()


async def test_start_raises_when_nothing_connects():
    srv = FakeServer(down=True)
    pool = SupervisedMcpPool(srv.connect, clients=2, backoff=FAST)
    with pytest.raises(McpUnavailable, match="connection refused"):
        await pool.start(wait=1, timeout=0.2)
    with pytest.raises(McpPoolClosed):
        await pool.call("echo", {})


async def test_timeout_covers_the_queue_and_the_call_and_frees_the_slot():
    srv = FakeServer()
    async with SupervisedMcpPool(srv.connect, clients=1, per_client=1, backoff=FAST) as pool:
        t = time.monotonic()
        hang = asyncio.create_task(pool.call("hang", {}, timeout=0.2))
        queued = asyncio.create_task(pool.call("echo", {}, timeout=0.1))  # waits behind `hang`
        with pytest.raises(TimeoutError):
            await queued
        assert 0.08 <= time.monotonic() - t <= 0.2
        with pytest.raises(TimeoutError):
            await hang
        assert 0.18 <= time.monotonic() - t <= 0.35
        assert (await pool.call("echo", {"next": 1}, timeout=1.0)).content == {"next": 1}
        assert srv.conns[0].cancelled == 1 and pool.counts["timed_out"] == 2
        assert len(srv.conns) == 1  # a timeout is not a connection failure


async def test_a_caller_that_stops_waiting_stops_its_call():
    srv = FakeServer()
    async with SupervisedMcpPool(srv.connect, clients=1, per_client=1, backoff=FAST) as pool:
        task = asyncio.create_task(pool.call("hang", {}))
        await until(lambda: srv.active == 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await until(lambda: srv.active == 0)
        assert srv.conns[0].cancelled == 1 and pool.counts["abandoned"] == 1
        assert (await pool.call("echo", {}, timeout=1.0)).content == {}


async def test_a_fixed_sdk_reports_lost_calls_and_the_client_restarts_after_several():
    srv = FakeServer()
    async with SupervisedMcpPool(srv.connect, clients=1, per_client=1, backoff=FAST, max_losses=3) as pool:
        for name in ["lost", "lost", "echo", "lost", "lost"]:
            if name == "lost":
                with pytest.raises(McpConnectionLost):
                    await pool.call(name, {})
            else:
                await pool.call(name, {})
        assert pool.counts["restarts"] == 0 and len(srv.conns) == 1  # an answer reset the count
        with pytest.raises(McpConnectionLost):
            await pool.call("lost", {})
        await until(lambda: len(srv.conns) == 2 and pool.stats()["connected"] == 1)
        assert pool.counts["restarts"] == 1 and pool.counts["connection_lost"] == 5


async def test_only_idempotent_calls_are_sent_again():
    srv = FakeServer()
    async with SupervisedMcpPool(srv.connect, clients=1, per_client=1, backoff=FAST) as pool:
        with pytest.raises(McpConnectionLost):
            await pool.call("flaky", {"key": "a"})
        assert (await pool.call("flaky", {"key": "b"}, idempotent=True)).content == {"key": "b"}
        assert pool.counts["resent"] == 1 and pool.counts["restarts"] == 2


async def test_tool_and_server_errors_belong_to_their_call():
    srv = FakeServer()
    async with SupervisedMcpPool(srv.connect, clients=1, per_client=2, backoff=FAST) as pool:
        assert (await pool.call("tool_error", {})).is_error
        with pytest.raises(MCPError, match="bad arguments"):
            await pool.call("bad", {})
        assert (await pool.call("echo", {"ok": 1})).content == {"ok": 1}
        assert pool.counts["restarts"] == 0 and len(srv.conns) == 1
        assert pool.counts["tool_error"] == 1 and pool.counts["error"] == 1 and pool.counts["ok"] == 1


async def test_clients_take_the_servers_in_turn_and_share_one_queue():
    a, b = FakeServer(), FakeServer()
    async with SupervisedMcpPool([a.connect, b.connect], clients=4, per_client=2, backoff=FAST) as pool:
        await until(lambda: len(a.live()) == 2 and len(b.live()) == 2)
        out = await asyncio.gather(*(pool.call("slow", {"s": 0.01, "i": i}) for i in range(40)))
        assert [r.content["i"] for r in out] == list(range(40))
        stats = pool.stats()
        assert stats["servers"] == 2 and sum(stats["served_per_server"].values()) == 40
        assert all(n > 0 for n in stats["served_per_server"].values())
        assert a.max_active <= 4 and b.max_active <= 4  # two clients of two slots each


async def test_a_server_that_is_down_holds_no_calls():
    up, down = FakeServer(), FakeServer(down=True)
    async with SupervisedMcpPool([up.connect, down.connect], clients=2, per_client=2, backoff=FAST) as pool:
        out = await asyncio.gather(*(pool.call("echo", {"i": i}) for i in range(10)))
        assert [r.content for r in out] == [{"i": i} for i in range(10)]
        assert list(pool.stats()["served_per_server"].values()) == [10, 0]
        assert pool.counts["connect_failures"] >= 1
        down.down = False  # it comes back, and takes calls again
        await until(lambda: pool.stats()["connected"] == 2)
        await asyncio.gather(*(pool.call("slow", {"s": 0.01}) for _ in range(20)))
        assert list(pool.stats()["served_per_server"].values())[1] > 0


def test_needs_a_client_per_server():
    with pytest.raises(ValueError, match="at least one per server"):
        SupervisedMcpPool(["http://127.0.0.1:1/mcp", "http://127.0.0.1:2/mcp"], clients=1)
    pool = SupervisedMcpPool(["http://127.0.0.1:1/mcp", "http://127.0.0.1:2/mcp"], clients=2)
    assert pool.servers == ["http://127.0.0.1:1/mcp", "http://127.0.0.1:2/mcp"]
