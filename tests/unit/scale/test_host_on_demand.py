"""Agents on demand: a started host takes agents one at a time with ``submit``
and ends with ``stop``, keeping the cap, staggered starts, the collector,
suspension, the accounting and ``on_done``; with a fake runner, Flowgentic's
loop and LangGraph's graphs."""

import asyncio
import contextvars
import gc
import logging
import random
import sys
import threading
import time

import pytest

from flowgentic import _agents
from flowgentic.host import (
    Agent,
    AgentHost,
    AgentResult,
    AgentState,
    Collector,
    Finished,
    Interrupted,
    QueueFull,
    Stagger,
    Suspended,
    current_agent,
    register_pending,
)
from flowgentic.host import _freeze_owners
from flowgentic.host._probes import _collector

FAST = Stagger(batch=50, interval_s=0.0)
LIVE = (AgentState.WORKING, AgentState.TOOL_CALL, AgentState.MODEL_QUEUED,
        AgentState.MODEL_REQUEST, AgentState.TOOL_WAIT)


class Runner:
    """Each agent sleeps its input's seconds, or ``sleep``. Agents whose id
    starts with ``x`` raise, ``h`` stop on an interrupt, and ``s`` suspend
    once on a task, ``self.tasks[id]``, that ends after ``release_after``
    seconds, or when the test ends it if that is ``None``."""

    def __init__(self, sleep: float = 0.01, release_after: float | None = 0.02):
        self.sleep = sleep
        self.release_after = release_after
        self.host: AgentHost | None = None
        self.started: list[str] = []
        self.starts: list[float] = []
        self.most = 0
        self.tasks: dict[str, asyncio.Future] = {}

    def live(self) -> int:
        counts = self.host.states.counts()
        return sum(counts[s] for s in LIVE)

    async def start(self, agent):
        loop = asyncio.get_running_loop()
        self.started.append(agent.id)
        self.starts.append(loop.time())
        self.most = max(self.most, self.live())
        await asyncio.sleep(self.sleep if agent.input is None else agent.input)
        if agent.id.startswith("x"):
            raise ValueError("bad agent")
        if agent.id.startswith("h"):
            return Interrupted(("ask a human",))
        if agent.id.startswith("s"):
            future = loop.create_future()
            self.tasks[agent.id] = future
            if self.release_after is not None:
                loop.call_later(self.release_after, lambda: future.done() or future.set_result(7))
            register_pending(f"task/{agent.id}", future)
            return Suspended((("i1", f"task/{agent.id}"),))
        return Finished({"agent": agent.id})

    async def resume(self, agent, done):
        assert done == {"i1": {"flowgentic_done": f"task/{agent.id}"}}
        return Finished({"agent": agent.id, "resumed": True})


def make_host(runner: Runner, **kwargs) -> AgentHost:
    kwargs.setdefault("stagger", FAST)
    kwargs.setdefault("collector", None)
    host = AgentHost(runner, **kwargs)
    runner.host = host
    return host


def nothing_left_frozen() -> bool:
    gc.collect()
    frozen = gc.get_freeze_count()
    gc.unfreeze()
    gc.collect()
    return frozen == gc.get_freeze_count()


# --- the cap, the order, and what each future resolves to ---------------------------


async def test_agents_submitted_over_time_stay_under_the_cap():
    runner = Runner(sleep=0.02)
    seen = []
    host = make_host(runner, max_live_agents=5, on_done=lambda r, out: seen.append(r.id))
    await host.start()
    futures = []
    for burst in range(10):
        for i in range(5):
            futures.append(host.submit(Agent(f"a{burst}-{i}", None)))
        await asyncio.sleep(0.005 * (burst % 3))
    results = await asyncio.gather(*futures)
    report = await host.stop()

    assert runner.most <= 5
    assert [r.status for r, _ in results] == ["done"] * 50
    assert [out for _, out in results] == [{"agent": f.result()[0].id} for f in futures]
    assert sorted(seen) == sorted(f"a{b}-{i}" for b in range(10) for i in range(5))
    assert report.outcomes == {"done": 50} and report.finished == 50
    assert report.agents == []  # each result went to its future and to on_done
    assert host.states.counts()[AgentState.DONE] == 50
    assert not host.states._state  # nothing is kept of an agent that ended


async def test_waiting_agents_start_in_the_order_they_came():
    runner = Runner(sleep=0.01)
    host = make_host(runner, max_live_agents=1)
    await host.start()
    order = [f"a{i}" for i in (3, 1, 4, 1.5, 9, 2, 6)]
    futures = [host.submit(Agent(key, None)) for key in order]
    await asyncio.gather(*futures)
    await host.stop()
    assert runner.started == order


async def test_each_future_resolves_to_what_on_done_receives():
    received = {}
    host = make_host(Runner(), on_done=lambda r, out: received.__setitem__(r.id, (r, out)))
    await host.start()
    futures = {key: host.submit(Agent(key, None)) for key in ("a1", "s1", "x1", "h1")}
    await asyncio.gather(*futures.values())
    report = await host.stop()

    for key, future in futures.items():
        result, output = future.result()
        assert (result, output) == received[key] and result is received[key][0]
    done, _ = futures["a1"].result()
    assert done.status == "done" and done.t_end >= done.t_start > 0
    suspended, output = futures["s1"].result()
    assert suspended.status == "done" and suspended.suspensions == 1
    assert output == {"agent": "s1", "resumed": True}
    # An agent's error is its result's, never the future's.
    failed, output = futures["x1"].result()
    assert failed.status == "failed" and failed.error == "ValueError: bad agent"
    assert output is None
    interrupted, _ = futures["h1"].result()
    assert interrupted.status == "interrupted"
    assert interrupted.interrupts == ("ask a human",)
    assert report.outcomes == {"done": 2, "failed": 1, "interrupted": 1}
    assert report.errors == {"ValueError": 1}


async def test_a_failing_on_done_fails_the_agent_and_its_future_says_so():
    def on_done(result, _output):
        if result.id == "a2":
            raise RuntimeError("sink down")

    host = make_host(Runner(), on_done=on_done)
    await host.start()
    first, second = host.submit(Agent("a1", None)), host.submit(Agent("a2", None))
    (ok, _), (bad, output) = await asyncio.gather(first, second)
    report = await host.stop()
    assert ok.status == "done"
    assert bad.status == "failed" and bad.error is None
    assert bad.callback_error == "RuntimeError: sink down"
    assert output == {"agent": "a2"}  # what on_done received
    assert report.outcomes == {"done": 1, "failed": 1}
    assert report.errors == {"RuntimeError": 1}


# --- refusing work ---------------------------------------------------------------------


async def test_max_queued_refuses_work_beyond_the_live_agents_and_the_queue():
    runner = Runner(sleep=0.05)
    host = make_host(runner, max_live_agents=2, max_queued=3)
    await host.start()
    futures = [host.submit(Agent(f"a{i}", None)) for i in range(5)]
    with pytest.raises(QueueFull, match="max_live_agents \\+ max_queued = 5"):
        host.submit(Agent("a5", None))
    await futures[0]
    futures.append(host.submit(Agent("a5", None)))  # a slot freed
    await asyncio.gather(*futures)
    report = await host.stop()
    assert report.outcomes == {"done": 6}


async def test_max_queued_zero_takes_an_agent_only_into_a_free_slot():
    host = make_host(Runner(sleep=0.05), max_live_agents=2, max_queued=0)
    await host.start()
    first = [host.submit(Agent(f"a{i}", None)) for i in range(2)]
    with pytest.raises(QueueFull):
        host.submit(Agent("a2", None))
    await asyncio.gather(*first)
    await host.submit(Agent("a2", None))
    assert (await host.stop()).outcomes == {"done": 3}


def test_a_negative_max_queued_is_refused():
    with pytest.raises(ValueError, match="max_queued"):
        AgentHost(Runner(), max_queued=-1)


async def test_an_id_is_refused_while_live_or_waiting_and_taken_again_after():
    runner = Runner(sleep=0.05)
    host = make_host(runner, max_live_agents=1)
    await host.start()
    live = host.submit(Agent("same", None))
    waiting = host.submit(Agent("next", None))
    with pytest.raises(ValueError, match="'same' is live or waiting"):
        host.submit(Agent("same", None))  # live
    with pytest.raises(ValueError, match="'next' is live or waiting"):
        host.submit(Agent("next", None))  # waiting
    await live
    again = host.submit(Agent("same", None))  # its agent has ended
    await asyncio.gather(waiting, again)
    report = await host.stop()
    assert runner.started == ["same", "next", "same"]
    assert report.outcomes == {"done": 3}
    assert host.states.counts()[AgentState.DONE] == 3


async def test_an_id_is_free_again_when_its_future_resolves():
    """A server may run one logical agent once per message: the next message's
    agent can be submitted as soon as the last one's future resolves."""
    host = make_host(Runner(sleep=0.0))
    await host.start()
    for _ in range(20):
        result, _ = await host.submit(Agent("agent/task", None))
        assert result.status == "done"
    assert (await host.stop()).outcomes == {"done": 20}


async def test_submit_before_start_after_stop_and_during_run_is_refused():
    host = make_host(Runner(sleep=0.05))
    with pytest.raises(RuntimeError, match="not running"):
        host.submit(Agent("a", None))
    await host.start()
    with pytest.raises(RuntimeError, match="running already"):
        await host.start()
    with pytest.raises(RuntimeError, match="running already"):
        await host.run([Agent("b", None)])
    future = host.submit(Agent("a", None))
    stopping = asyncio.create_task(host.stop())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="stopping"):
        host.submit(Agent("c", None))
    await stopping
    assert future.result()[0].status == "done"
    with pytest.raises(RuntimeError, match="not running"):
        host.submit(Agent("d", None))
    with pytest.raises(RuntimeError, match="not running"):
        await host.stop()

    run = asyncio.create_task(host.run([Agent("e", None)]))
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="fixed set of agents"):
        host.submit(Agent("f", None))
    assert (await run).finished == 1


async def test_submit_from_another_event_loop_is_refused():
    host = make_host(Runner())
    await host.start()
    errors = []

    def other_thread():
        async def submit():
            host.submit(Agent("a", None))

        try:
            asyncio.run(submit())
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=other_thread)
    thread.start()
    thread.join()
    await host.stop()
    assert errors == ["submit agents on the event loop the host runs on"]


# --- stopping ------------------------------------------------------------------------------


async def test_stop_with_drain_waits_for_live_and_waiting_agents():
    runner = Runner(sleep=0.03)
    host = make_host(runner, max_live_agents=2)
    await host.start()
    futures = [host.submit(Agent(f"{p}{i}", None)) for i, p in enumerate("asasas")]
    report = await host.stop()
    assert all(f.done() for f in futures)
    assert report.outcomes == {"done": 6}
    assert sorted(runner.started) == sorted(f"{p}{i}" for i, p in enumerate("asasas"))


async def test_stop_without_drain_cancels_live_agents_and_withdraws_waiting_ones():
    runner = Runner(sleep=10)
    seen = []
    host = make_host(
        runner, max_live_agents=2, on_done=lambda r, _out: seen.append((r.id, r.status))
    )
    await host.start()
    futures = [host.submit(Agent(f"a{i}", None)) for i in range(5)]
    await asyncio.sleep(0.02)
    t0 = time.monotonic()
    report = await host.stop(drain=False)
    assert time.monotonic() - t0 < 1

    results = [f.result()[0] for f in futures]
    assert [r.status for r in results] == ["cancelled"] * 5
    # The two live agents ran, and on_done received them; the three waiting never started.
    assert runner.started == ["a0", "a1"]
    assert sorted(seen) == [("a0", "cancelled"), ("a1", "cancelled")]
    assert all(r.t_start > 0 for r in results[:2])
    assert all(r.t_start == 0 and r.error is None for r in results[2:])
    assert report.outcomes == {"cancelled": 5}
    counts = host.states.counts()
    assert counts[AgentState.DONE] == 5 and counts[AgentState.PENDING] == 0
    assert not host.states._state


@pytest.mark.parametrize("drain", [True, False])
async def test_a_host_stopped_at_once_after_start_ends(drain):
    """Before the host's own task has taken a step, with an agent or none."""
    for submit in (False, True):
        host = make_host(Runner())
        await host.start()
        future = host.submit(Agent("a", None)) if submit else None
        async with asyncio.timeout(5):  # in this task: no step in between
            report = await host.stop(drain=drain)
        expected = {"done": 1} if submit and drain else {"cancelled": 1} if submit else {}
        assert report.outcomes == expected
        assert future is None or future.done()


async def test_an_agent_cannot_stop_its_own_host():
    class Stops(Runner):
        async def start(self, agent):
            await self.host.stop()

    host = make_host(Stops())
    await host.start()
    result, _ = await host.submit(Agent("a", None))
    await host.stop()
    assert result.status == "failed"
    assert result.error == "RuntimeError: an agent cannot stop the host it runs in"


async def test_stop_without_drain_while_a_stop_drains_cancels_what_is_left():
    runner = Runner(sleep=10)
    host = make_host(runner, max_live_agents=1)
    await host.start()
    futures = [host.submit(Agent(f"a{i}", None)) for i in range(3)]
    draining = asyncio.create_task(host.stop())
    done, _ = await asyncio.wait({draining}, timeout=0.05)
    assert not done  # a0 runs for 10 s
    report = await host.stop(drain=False)
    assert await draining is report
    assert report.outcomes == {"cancelled": 3}
    assert [f.result()[0].status for f in futures] == ["cancelled"] * 3


async def test_cancelling_stop_ends_the_host_as_cancelling_run_does():
    before = gc.get_threshold()
    runner = Runner(sleep=10)
    seen = []
    host = make_host(
        runner,
        max_live_agents=2,
        collector=Collector(),
        on_done=lambda r, _out: seen.append(r.status),
    )
    await host.start()
    futures = [host.submit(Agent(f"a{i}", None)) for i in range(4)]
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.1):
            await host.stop()
    assert seen == ["cancelled", "cancelled"]
    assert [f.result()[0].status for f in futures] == ["cancelled"] * 4
    assert gc.get_threshold() == before and nothing_left_frozen()
    with pytest.raises(RuntimeError, match="not running"):
        host.submit(Agent("b", None))
    await host.start()  # and it starts again
    await host.submit(Agent("b", 0.0))
    assert (await host.stop()).outcomes == {"done": 1}


async def test_a_host_serves_runs_and_serves_again():
    runner = Runner(sleep=0.0)
    host = make_host(runner)
    await host.start()
    await host.submit(Agent("a", None))
    first = await host.stop()
    second = await host.run([Agent("a", None), Agent("b", None)])
    await host.start()
    await host.submit(Agent("a", None))
    third = await host.stop()
    assert first.outcomes == third.outcomes == {"done": 1}
    assert second.finished == 2 and [r.id for r in second.agents] == ["a", "b"]
    assert host.states.counts()[AgentState.DONE] == 1  # each period counts afresh


async def test_stop_is_refused_while_a_run_runs():
    """``stop`` ends what ``start`` began; a run ends when its agents have, or
    when it is cancelled, and runs on, unchanged, after the refusal."""
    runner = Runner(sleep=0.02)
    host = make_host(runner, max_live_agents=2)
    run = asyncio.create_task(host.run([Agent(f"a{i}", None) for i in range(5)]))
    await asyncio.sleep(0.01)
    for drain in (True, False):
        with pytest.raises(RuntimeError, match="fixed set of agents"):
            await host.stop(drain=drain)
    report = await run
    assert report.finished == 5
    indexed = asyncio.create_task(host.run_indexed(range(3), lambda i: None))
    await asyncio.sleep(0.005)
    with pytest.raises(RuntimeError, match="fixed set of agents"):
        await host.stop()
    assert (await indexed).outcomes == {"done": 3}


# --- cancelling one agent ----------------------------------------------------------------


async def test_cancelling_a_future_cancels_its_agent():
    runner = Runner(sleep=10)
    seen = []
    host = make_host(
        runner, max_live_agents=1, on_done=lambda r, _out: seen.append((r.id, r.status))
    )
    await host.start()
    live = host.submit(Agent("live", None))
    waiting = host.submit(Agent("waiting", None))
    later = host.submit(Agent("later", 0.0))
    await asyncio.sleep(0.02)
    waiting.cancel()  # it never starts
    live.cancel()  # it ends as cancelled
    await later
    report = await host.stop()
    assert runner.started == ["live", "later"]
    assert seen == [("live", "cancelled"), ("later", "done")]
    assert report.outcomes == {"cancelled": 2, "done": 1}
    assert not host.states._state


async def test_a_handler_cancelled_while_it_awaits_cancels_its_agent():
    """``await`` on the future in a task that is cancelled cancels the future,
    and so the agent: shield it to keep the agent running."""
    runner = Runner(sleep=10)
    host = make_host(runner)
    await host.start()

    async def handler(key, shield):
        future = host.submit(Agent(key, None))
        return await (asyncio.shield(future) if shield else future)

    plain = asyncio.create_task(handler("plain", False))
    shielded = asyncio.create_task(handler("shielded", True))
    await asyncio.sleep(0.02)
    plain.cancel()
    shielded.cancel()
    await asyncio.gather(plain, shielded, return_exceptions=True)
    await asyncio.sleep(0.01)
    assert host.states.counts()[AgentState.WORKING] == 1  # the shielded one runs on
    report = await host.stop(drain=False)
    assert report.outcomes == {"cancelled": 2}


# --- suspension, staggered starts, the collector and the accounting ---------------------


async def test_suspended_agents_keep_their_slots_while_others_arrive():
    runner = Runner(sleep=0.0, release_after=None)
    host = make_host(runner, max_live_agents=2)
    await host.start()
    suspended = [host.submit(Agent(f"s{i}", None)) for i in range(2)]
    await asyncio.sleep(0.02)
    arriving = [host.submit(Agent(f"a{i}", None)) for i in range(3)]
    await asyncio.sleep(0.02)
    counts = host.states.counts()
    assert counts[AgentState.TOOL_WAIT] == 2 and counts[AgentState.PENDING] == 3
    assert runner.started == ["s0", "s1"]  # both hold their slots while suspended

    runner.tasks["s1"].set_result(1)  # s1 resumes and ends: a0 takes its slot
    await suspended[1]
    await asyncio.sleep(0.02)
    assert runner.started == ["s0", "s1", "a0", "a1", "a2"]
    assert host.states.counts()[AgentState.TOOL_WAIT] == 1
    more = host.submit(Agent("s2", None))  # suspends beside s0
    await asyncio.sleep(0.02)
    assert host.states.counts()[AgentState.TOOL_WAIT] == 2
    runner.tasks["s0"].set_result(0)
    runner.tasks["s2"].set_result(2)
    results = await asyncio.gather(*suspended, *arriving, more)
    report = await host.stop()
    assert [r.suspensions for r, _ in results] == [1, 1, 0, 0, 0, 1]
    assert all(out.get("resumed") for r, out in results if r.id.startswith("s"))
    assert report.outcomes == {"done": 6}
    assert report.states["agent_seconds"]["tool_wait"] > 0
    assert all(_agents.pending(f"task/s{i}") is None for i in range(3))


async def test_the_states_count_waiting_live_and_ended_agents():
    runner = Runner(sleep=0.05)
    host = make_host(runner, max_live_agents=3)
    await host.start()
    futures = [host.submit(Agent(f"a{i}", None)) for i in range(8)]
    await asyncio.sleep(0.01)
    during = host.states.counts()
    await asyncio.gather(*futures)
    after = host.states.counts()
    report = await host.stop()
    assert during[AgentState.WORKING] == 3 and during[AgentState.PENDING] == 5
    assert after[AgentState.DONE] == 8 and after[AgentState.PENDING] == 0
    assert report.states["agent_seconds"]["pending"] > 0


async def test_the_first_wave_of_submissions_is_staggered():
    runner = Runner(sleep=0.3)
    host = make_host(runner, max_live_agents=6, stagger=Stagger(batch=2, interval_s=0.1))
    await host.start()
    futures = [host.submit(Agent(f"a{i}", None)) for i in range(8)]
    await asyncio.gather(*futures)
    await host.stop()
    t = runner.starts
    assert t[1] - t[0] < 0.05  # one batch together
    assert t[2] - t[1] > 0.08 and t[4] - t[3] > 0.08  # batches apart
    # a6 and a7 come after the first wave: each starts as a slot frees.
    assert t[7] - t[6] < 0.05


async def test_the_collector_is_tuned_while_the_host_serves_and_restored():
    before = gc.get_threshold()
    seen = {}

    class Probe(Runner):
        async def start(self, agent):
            await asyncio.sleep(0.02)
            seen[agent.id] = (gc.get_threshold()[0], bool(_freeze_owners))
            return Finished(None)

    host = make_host(Probe(), collector=Collector())
    await host.start()
    assert gc.get_threshold()[0] == 100_000
    await host.submit(Agent("first", None))
    await asyncio.sleep(0.05)
    await host.submit(Agent("later", None))
    report = await host.stop()
    # The host froze once its first agent had started, and thawed at stop.
    assert seen == {"first": (100_000, True), "later": (100_000, True)}
    freeze = report.collector["freeze"]
    assert freeze["frozen"] > freeze["frozen_before"]
    assert report.collector["settings"] == {"threshold": 100_000, "freeze": True}
    assert gc.get_threshold() == before and nothing_left_frozen()


async def test_an_agent_runs_in_a_copy_of_the_submitters_context():
    request = contextvars.ContextVar("request", default=None)
    seen = {}

    class Reads(Runner):
        async def start(self, agent):
            seen[agent.id] = (request.get(), current_agent())
            request.set("changed by the agent")
            return Finished(None)

    host = make_host(Reads(), max_live_agents=1)
    await host.start()
    request.set("r1")
    first = host.submit(Agent("a1", None))
    request.set("r2")
    second = host.submit(Agent("a2", None))  # waits for a1's slot
    request.set("r3")
    await asyncio.gather(first, second)
    await host.stop()
    assert seen == {"a1": ("r1", "a1"), "a2": ("r2", "a2")}
    assert request.get() == "r3"


async def test_an_agent_submits_another_and_awaits_it():
    """In one process, an agent calls another agent through the same host."""

    class Delegates(Runner):
        async def start(self, agent):
            if not agent.id.endswith("/helper"):
                result, output = await self.host.submit(Agent(f"{agent.id}/helper", None))
                return Finished({"helper": output, "me": current_agent()})
            return Finished(current_agent())

    runner = Delegates()
    host = make_host(runner, max_live_agents=4)
    await host.start()
    bosses = [host.submit(Agent(f"boss{i}", None)) for i in range(3)]
    outputs = [out for _, out in await asyncio.gather(*bosses)]
    await host.stop()
    assert outputs == [{"helper": f"boss{i}/helper", "me": f"boss{i}"} for i in range(3)]


# --- both runners ---------------------------------------------------------------------------


async def test_loop_runner_agents_submitted_over_time_suspend_and_resume():
    from _basics import RUNS, simulate, thread_session, transport

    from flowgentic.loop import LoopOutput, LoopRunner
    from flowgentic.store import MemoryStore
    from flowgentic.tools import ToolRuntime, task_tool

    RUNS.clear()
    session = await thread_session()
    runtime = ToolRuntime(session)
    store = MemoryStore()
    runner = LoopRunner(
        transport(("simulate", {"x": 1.5})), [task_tool(simulate, suspend=True)],
        runtime=runtime, store=store,
    )  # fmt: skip
    host = AgentHost(runner, max_live_agents=5, stagger=FAST)
    await host.start()
    futures = []
    for i in range(30):
        futures.append(host.submit(Agent(f"agent-{i}", "go")))
        if i % 4 == 3:
            await asyncio.sleep(0.01)
    results = await asyncio.gather(*futures)
    report = await host.stop()
    await session.close()

    assert report.outcomes == {"done": 30}
    assert all(r.suspensions == 1 for r, _ in results)
    assert all(isinstance(out, LoopOutput) for _, out in results)
    assert all('{"y": 3.0}' in out.messages[-1]["content"] for _, out in results)
    assert RUNS == {"simulate": 30}  # each task ran once
    assert runtime.stats()["repeats"] == 30
    assert store.keys() == []  # each agent's state deleted when it ended
    assert report.states["agent_seconds"]["tool_wait"] > 0


async def test_an_id_run_again_on_the_loop_makes_its_calls_afresh():
    """A server runs one logical agent once per message, under one id. A model
    server that numbers its calls per reply gives each run the same call ids:
    each run's calls are new tasks, not the earlier run's results."""
    from _basics import RUNS, simulate, thread_session, transport

    from flowgentic.loop import LoopRunner
    from flowgentic.store import MemoryStore
    from flowgentic.tools import ToolRuntime, task_tool

    RUNS.clear()
    session = await thread_session()
    runtime = ToolRuntime(session)
    runner = LoopRunner(
        transport(("simulate", {"x": 1.5})), [task_tool(simulate, suspend=True)],
        runtime=runtime, store=MemoryStore(),
    )  # fmt: skip
    host = AgentHost(runner, stagger=FAST)
    await host.start()
    for _ in range(3):
        result, _ = await host.submit(Agent("agent/task", "go"))
        assert result.status == "done" and result.suspensions == 1
    await host.stop()
    await session.close()
    assert RUNS == {"simulate": 3}
    assert runtime.stats()["submitted"] == 3


async def test_graph_runner_agents_submitted_over_time_suspend_and_resume():
    pytest.importorskip("langgraph")
    from _support import RUNS, Scripted, calls_then_answer, react_graph, simulate, thread_session
    from langchain_core.messages import HumanMessage

    from flowgentic.langgraph import GraphRunner, KeyValueSaver, bind_tools
    from flowgentic.store import MemoryStore
    from flowgentic.tools import ToolRuntime, task_tool

    RUNS.clear()
    session = await thread_session()
    runtime = ToolRuntime(session)
    graph = react_graph(
        Scripted(script=calls_then_answer(("simulate", {"x": 1.5}))),
        bind_tools(runtime, [task_tool(simulate, suspend=True)]),
        KeyValueSaver(MemoryStore()),
    )
    host = AgentHost(GraphRunner(graph, delete_finished=True), max_live_agents=5, stagger=FAST)
    await host.start()
    futures = []
    for i in range(30):
        futures.append(host.submit(Agent(f"agent-{i}", {"messages": [HumanMessage("go")]})))
        if i % 4 == 3:
            await asyncio.sleep(0.01)
    results = await asyncio.gather(*futures)
    # The same agent again, for a second message: a fresh thread.
    again, out = await host.submit(Agent("agent-0", {"messages": [HumanMessage("go")]}))
    report = await host.stop()
    await session.close()

    assert report.outcomes == {"done": 31} and again.status == "done"
    assert all(r.suspensions == 1 for r, _ in results)
    assert all('{"y": 3.0}' in o["messages"][-1].content for _, o in results)
    assert len(out["messages"]) == 4  # human, call, result, answer: no earlier turn
    assert RUNS == {"simulate": 31}


# --- delegation while a stop drains, and a host left unstopped -------------------------------


async def test_agents_still_delegate_while_stop_drains():
    """A boss agent that submits a helper while the host drains ends as it
    would have, and so does an ``on_done`` that submits a follow-up. Code that
    is not one of the host's agents is refused, an agent of another host
    included."""

    class Boss(Runner):
        async def start(self, agent):
            if "/" in agent.id:  # a helper or a follow-up
                return Finished(agent.id)
            await asyncio.sleep(0.03)  # the host drains by now
            _, output = await self.host.submit(Agent(f"{agent.id}/helper", None))
            return Finished(output)

    class Stranger(Runner):
        async def start(self, agent):
            await asyncio.sleep(0.01)
            host.submit(Agent("from-another-host", None))

    follow_ups = []

    def on_done(result, _out):
        if "/" not in result.id:
            follow_ups.append(host.submit(Agent(f"{result.id}/next", None)))

    host = make_host(Boss(), max_live_agents=4, on_done=on_done)
    other = make_host(Stranger())
    await host.start()
    await other.start()
    bosses = [host.submit(Agent(f"boss{i}", None)) for i in range(3)]
    stranger = other.submit(Agent("stranger", None))
    stopping = asyncio.create_task(host.stop())
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="stopping"):
        host.submit(Agent("outsider", None))
    report = await stopping
    stranger_result, _ = await stranger
    await other.stop()

    assert [f.result()[1] for f in bosses] == [f"boss{i}/helper" for i in range(3)]
    assert [f.result()[1] for f in follow_ups] == [f"boss{i}/next" for i in range(3)]
    assert report.outcomes == {"done": 9}
    assert stranger_result.status == "failed"
    assert stranger_result.error == "RuntimeError: this host is stopping"
    with pytest.raises(RuntimeError, match="not running"):
        host.submit(Agent("late", None))


async def test_on_done_may_submit_a_follow_up_before_and_while_stop_drains():
    follow_ups = []

    def on_done(result, _out):
        if result.id.count("/") < 3:
            follow_ups.append(host.submit(Agent(f"{result.id}/next", None)))

    host = make_host(Runner(sleep=0.01), on_done=on_done)
    await host.start()
    await host.submit(Agent("a", None))  # its on_done has run: a/next is waiting or live
    assert len(follow_ups) == 1
    report = await host.stop()  # the rest of the chain runs while it drains
    assert [f.result()[0].id for f in follow_ups] == ["a/next", "a/next/next", "a/next/next/next"]
    assert report.outcomes == {"done": 4}


async def test_stop_without_drain_cancels_a_chain_that_a_drain_let_grow():
    class Boss(Runner):
        async def start(self, agent):
            if "/" in agent.id:
                await asyncio.sleep(10)
                return Finished(None)
            await asyncio.sleep(0.01)
            result, _ = await self.host.submit(Agent(f"{agent.id}/helper", None))
            return Finished(result.status)

    host = make_host(Boss(), max_live_agents=2)
    await host.start()
    boss = host.submit(Agent("boss", None))
    draining = asyncio.create_task(host.stop())
    await asyncio.sleep(0.05)
    assert host.states.counts()[AgentState.WORKING] == 2  # the boss and its helper
    report = await host.stop(drain=False)
    assert await draining is report
    assert report.outcomes == {"cancelled": 2}
    assert boss.result()[0].status == "cancelled"


async def test_a_task_an_agent_starts_cannot_stop_the_host_while_the_agent_runs():
    """It would wait for the agent's end, and the agent for it. Once the agent
    has ended, as in a task its on_done starts, ``stop`` works."""

    class Stops(Runner):
        async def start(self, agent):
            if agent.id == "child":
                await asyncio.create_task(self.host.stop())
            return Finished(None)

    stops = []

    def on_done(result, _out):
        if result.id == "last":
            stops.append(asyncio.ensure_future(host.stop()))

    host = make_host(Stops(), on_done=on_done)
    await host.start()
    result, _ = await host.submit(Agent("child", None))
    assert result.status == "failed"
    assert result.error == "RuntimeError: an agent cannot stop the host it runs in"
    await host.submit(Agent("last", None))
    report = await stops[0]
    assert report.outcomes == {"failed": 1, "done": 1}
    assert host._session is None


async def test_cancelled_waiting_agents_leave_the_queue_at_once():
    """100,000 submit-and-cancel calls while the only slot is held: each
    withdrawn agent leaves the queue, with its future and its context, one
    loop turn after its cancel, so the queue does not grow."""
    host = make_host(Runner(sleep=10), max_live_agents=1)
    await host.start()
    live = host.submit(Agent("live", None))
    await asyncio.sleep(0.01)
    session = host._session
    most = 0
    for burst in range(1_000):
        for i in range(100):
            host.submit(Agent(f"w{i}", None)).cancel()  # ids used again each burst
        most = max(most, len(session.queue))
        await asyncio.sleep(0)
        assert not session.queue
    assert most == 100
    assert session.ids == {"live"} and set(host.states._state) == {"live"}
    live.cancel()
    report = await host.stop()
    assert report.outcomes == {"cancelled": 100_001}


async def test_a_cancelled_waiting_agents_id_and_place_free_one_loop_turn_later():
    host = make_host(Runner(sleep=10), max_live_agents=1, max_queued=1)
    await host.start()
    host.submit(Agent("live", None))
    waiting = host.submit(Agent("w", None))
    await asyncio.sleep(0.01)
    waiting.cancel()
    with pytest.raises(ValueError, match="live or waiting"):
        host.submit(Agent("w", None))  # the host learns of the cancel next turn
    with pytest.raises(QueueFull):
        host.submit(Agent("other", None))
    await asyncio.sleep(0)
    again = host.submit(Agent("w", None))
    report = await host.stop(drain=False)
    assert again.result()[0].status == "cancelled"
    assert report.outcomes == {"cancelled": 3}


async def test_async_with_starts_the_host_and_stops_it_at_the_end():
    host = make_host(Runner(sleep=0.01), max_live_agents=2)
    async with host as same:
        assert same is host
        futures = [host.submit(Agent(f"a{i}", None)) for i in range(5)]
    assert [f.result()[0].status for f in futures] == ["done"] * 5
    with pytest.raises(RuntimeError, match="not running"):
        host.submit(Agent("b", None))
    async with host:  # stopped inside: the block's end has nothing left to stop
        host.submit(Agent("b", None))
        report = await host.stop()
    assert report.outcomes == {"done": 1}


async def test_an_error_in_async_with_cancels_the_agents_and_restores_the_process():
    """The collector's threshold, the frozen objects, the collector's probe and
    the count of host runs are as before, and the host starts again."""
    before = (gc.get_threshold(), _agents._host_runs)
    runner = Runner(sleep=10)
    seen = []
    host = make_host(
        runner,
        max_live_agents=2,
        collector=Collector(),
        on_done=lambda r, _out: seen.append(r.status),
    )
    futures = []
    with pytest.raises(ZeroDivisionError):
        async with host:
            futures = [host.submit(Agent(f"a{i}", None)) for i in range(4)]
            await asyncio.sleep(0.02)
            assert host._session.freeze["frozen"] > 0  # the first wave has started
            assert gc.get_threshold()[0] == 100_000 and _collector.callback in gc.callbacks
            assert _agents._host_runs == before[1] + 1
            1 / 0  # noqa: B018
    assert [f.result()[0].status for f in futures] == ["cancelled"] * 4
    assert seen == ["cancelled", "cancelled"]  # the two live ones; two never started
    assert (gc.get_threshold(), _agents._host_runs) == before
    assert _collector.callback not in gc.callbacks
    assert not _freeze_owners and nothing_left_frozen()
    async with host:
        assert (await host.submit(Agent("b", 0.0)))[0].status == "done"


async def test_a_host_whose_own_task_dies_ends_its_waiting_agents(caplog):
    """Cancelled from outside, as by code that cancels every task: the host
    withdraws the waiting agents, refuses new ones, and lets the live ones end;
    ``stop`` still reports."""

    class Delegates(Runner):
        async def start(self, agent):
            await asyncio.sleep(0.05)
            if agent.id == "a0":  # while stop drains, with the host's task gone
                return Finished(self.host.submit(Agent("a0/helper", None)))
            return Finished(None)

    host = make_host(Delegates(), max_live_agents=2)
    await host.start()
    futures = [host.submit(Agent(f"a{i}", None)) for i in range(5)]
    await asyncio.sleep(0.01)
    with caplog.at_level(logging.WARNING, logger="flowgentic.host"):
        host._session.dispatcher.cancel()
        await asyncio.wait(futures[2:])
    assert [f.result()[0].status for f in futures[2:]] == ["cancelled"] * 3
    assert "own task ended before the host was stopped" in caplog.text
    with pytest.raises(RuntimeError, match="own task has ended"):
        host.submit(Agent("late", None))
    report = await host.stop()  # nothing starts a delegated agent any more
    assert [f.result()[0].status for f in futures] == ["failed"] + ["done"] + ["cancelled"] * 3
    assert futures[0].result()[0].error == "RuntimeError: this host is stopping"
    assert report.outcomes == {"failed": 1, "done": 1, "cancelled": 3}


def test_an_abandoned_host_ends_its_agents_futures_when_its_loop_ends():
    """A host started and never stopped, in an event loop that ends:
    ``asyncio.run`` cancels the host's task and its agents, and every future
    ends rather than hang. Only ``stop`` ends the host's period, which is why
    ``async with`` exists."""
    hosts, futures = [], []
    runs = _agents._host_runs

    async def main():
        host = make_host(Runner(sleep=10), max_live_agents=2)
        hosts.append(host)
        await host.start()
        futures.extend(host.submit(Agent(f"a{i}", None)) for i in range(4))
        await asyncio.sleep(0.01)  # and returns without stopping it

    asyncio.run(main())
    host = hosts[0]
    try:
        assert all(f.done() for f in futures)
        assert [f.result()[0].status for f in futures] == ["cancelled"] * 4
        assert _agents._host_runs == runs + 1  # the period never ended
    finally:
        host._end(host._session)  # what stop would have done
    assert _agents._host_runs == runs and _collector.callback not in gc.callbacks


@pytest.mark.skipif(sys.version_info < (3, 12), reason="eager tasks came in Python 3.12")
async def test_a_host_runs_under_an_eager_task_factory():
    """An eager task runs its first step inside ``create_task``, before the
    host has recorded it: an agent that ends there, submits there, or calls
    ``stop`` there (which the host refuses) behaves as under lazy tasks."""

    class Eager(Runner):
        async def start(self, agent):
            if agent.id == "stops":
                await self.host.stop()
            if agent.id == "boss":
                _, output = await self.host.submit(Agent("aide", None))
                return Finished(output)
            if agent.id.startswith("sync"):
                return Finished(agent.id)
            return await super().start(agent)

    loop = asyncio.get_running_loop()
    loop.set_task_factory(asyncio.eager_task_factory)
    try:
        host = make_host(Eager(sleep=0.0), max_live_agents=3)
        async with host:
            ids = ("sync0", "stops", "boss", "a0", "sync1")
            results = await asyncio.gather(*(host.submit(Agent(i, None)) for i in ids))
        assert [r.status for r, _ in results] == ["done", "failed", "done", "done", "done"]
        assert results[1][0].error == "RuntimeError: an agent cannot stop the host it runs in"
        assert results[2][1] == {"agent": "aide"}
        assert (await host.run([Agent("sync2", None), Agent("a1", None)])).finished == 2
        report = await host.run_indexed(range(3), lambda i: None, id_prefix="sync")
        assert report.outcomes == {"done": 3}
    finally:
        loop.set_task_factory(None)


async def test_submit_from_a_thread_goes_through_the_hosts_event_loop():
    """From a plain thread, ``submit`` raises and takes nothing;
    ``run_coroutine_threadsafe`` brings the call to the host's loop."""
    host = make_host(Runner(sleep=0.0))
    await host.start()
    loop = asyncio.get_running_loop()
    seen = {}

    def worker():
        try:
            host.submit(Agent("direct", None))
        except RuntimeError as exc:
            seen["direct"] = str(exc)

        async def through_the_loop():
            return await host.submit(Agent("via-loop", None))

        seen["via-loop"] = asyncio.run_coroutine_threadsafe(through_the_loop(), loop).result(5)

    await asyncio.to_thread(worker)
    report = await host.stop()
    assert seen["direct"] == "no running event loop"
    result, output = seen["via-loop"]
    assert result.status == "done" and output == {"agent": "via-loop"}
    assert report.outcomes == {"done": 1}


# --- at scale, and under stress -------------------------------------------------------------


async def test_ten_thousand_agents_submitted_over_time_in_one_process(capsys):
    """10,000 agents arrive in bursts while others run, suspend and end; a fifth
    suspend on a task. Prints the timings."""
    agents, cap, bursts = 10_000, 1_000, 50
    rng = random.Random(7)

    class Mixed:
        async def start(self, agent):
            await asyncio.sleep(agent.input)
            if agent.id.endswith(("0", "5")):
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                loop.call_later(rng.uniform(0.005, 0.03), future.set_result, 1)
                register_pending(f"task/{agent.id}", future)
                return Suspended((("i1", f"task/{agent.id}"),))
            return Finished(None)

        async def resume(self, agent, done):
            return Finished(None)

    ended = [0]
    host = AgentHost(
        Mixed(),
        max_live_agents=cap,
        stagger=Stagger(batch=500, interval_s=0.01),
        on_done=lambda r, _out: ended.__setitem__(0, ended[0] + 1),
    )
    await host.start()
    most, waited, submit_s, futures = 0, 0, 0.0, []
    t0 = time.perf_counter()
    for burst in range(bursts):
        t = time.perf_counter()
        for i in range(agents // bursts):
            futures.append(host.submit(Agent(f"a{burst}-{i}", rng.uniform(0.005, 0.05))))
        submit_s += time.perf_counter() - t
        counts = host.states.counts()
        most = max(most, sum(counts[s] for s in LIVE))
        waited = max(waited, counts[AgentState.PENDING])
        await asyncio.sleep(0.002)
    results = await asyncio.gather(*futures)
    report = await host.stop()
    wall = time.perf_counter() - t0

    assert report.outcomes == {"done": agents} and ended[0] == agents
    assert sum(r.suspensions for r, _ in results) == agents // 5
    assert most <= cap and waited > 0  # the arrivals outpace the agents: some wait
    assert not host.states._state and report.agents == []
    with capsys.disabled():
        print(
            f"\n{agents} agents submitted in {bursts} bursts: {wall:.2f} s wall, "
            f"{1e6 * submit_s / agents:.1f} µs per submit, "
            f"most live {most}, most waiting {waited}, loop lag p99 {report.loop_lag.get('time_weighted_p99_ms')} ms "
            f"(max {report.loop_lag.get('max_ms')} ms), "
            f"collector {report.collector['pauses']} pauses, {report.collector['total_s']} s"
        )


@pytest.mark.parametrize("seed", range(6))
async def test_stress_submits_cancels_and_stops_leave_nothing_behind(seed):
    """Random submissions, cancelled futures, failures, suspensions and stops,
    with and without draining: every future resolves or is cancelled, every
    agent is counted once, and no task, registration or collector setting
    outlives the host."""
    rng = random.Random(seed)
    before_threshold = gc.get_threshold()
    me = asyncio.current_task()

    class Chaotic:
        async def start(self, agent):
            await asyncio.sleep(rng.choice((0, 0, 0.001, 0.005, 0.02)))
            kind = agent.input
            if kind == "fail":
                raise ValueError("boom")
            if kind == "suspend":
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                loop.call_later(rng.uniform(0, 0.02), lambda: future.done() or future.set_result(1))
                register_pending(f"task/{agent.id}", future)
                return Suspended((("i1", f"task/{agent.id}"),))
            return Finished(kind)

        async def resume(self, agent, done):
            await asyncio.sleep(rng.choice((0, 0.002)))
            return Finished("resumed")

    for round_ in range(4):
        ended = []
        host = AgentHost(
            Chaotic(),
            max_live_agents=rng.choice((1, 3, 20)),
            max_queued=rng.choice((None, 0, 10)),
            stagger=Stagger(batch=rng.choice((1, 5, 50)), interval_s=rng.choice((0, 0.001))),
            collector=rng.choice((None, Collector())),
            on_done=lambda r, _out: ended.append(r.id),
        )
        await host.start()
        # Some rounds never yield: the host stops before its own task has run.
        quiet = rng.random() < 0.2
        futures, refused, cancelled_by_caller = [], 0, 0
        for i in range(rng.randrange(0, 10) if quiet else rng.randrange(50, 200)):
            kind = rng.choice(("ok", "ok", "fail", "suspend"))
            try:
                futures.append(host.submit(Agent(f"r{round_}-{i % 40}", kind)))
            except (QueueFull, ValueError):  # full, or the id is live or waiting
                refused += 1
            if rng.random() < 0.1 and futures:
                cancelled_by_caller += futures[rng.randrange(len(futures))].cancel()
            if not quiet and rng.random() < 0.2:
                await asyncio.sleep(rng.choice((0, 0.001, 0.005)))
        if quiet or rng.random() < 0.5:
            report = await host.stop(drain=rng.random() < 0.5)
        else:
            draining = asyncio.create_task(host.stop())
            await asyncio.sleep(rng.choice((0, 0.005)))
            if draining.done():  # it drained in time
                report = draining.result()
            else:
                report = await host.stop(drain=False)
                assert await draining is report

        assert all(f.done() for f in futures)
        assert sum(report.outcomes.values()) == len(futures)
        for future in futures:
            if not future.cancelled():
                result, _ = future.result()
                assert isinstance(result, AgentResult)
                assert result.status in ("done", "failed", "cancelled")
        assert set(report.outcomes) <= {"done", "failed", "cancelled"}
        assert not host.states._state
        assert host.states.counts()[AgentState.DONE] == len(futures)
        await asyncio.sleep(0)  # the lag probe's timer, cancelled at stop, ends
        assert asyncio.all_tasks() == {me}, asyncio.all_tasks()

    assert not _agents._pending and not _agents._unowned and not _agents._waits
    assert not _freeze_owners
    assert gc.get_threshold() == before_threshold
