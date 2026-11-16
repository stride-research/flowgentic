"""Agent states, and the agent host with a fake runner."""

import asyncio
import gc
import subprocess
import sys

import pytest

from flowgentic import _agents
from flowgentic.host import (
    Agent,
    AgentHost,
    AgentState,
    AgentStates,
    Collector,
    Finished,
    Interrupted,
    Stagger,
    Suspended,
    register_pending,
)

FAST = Stagger(batch=10, interval_s=0.0)


def test_states_count_and_time(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(_agents.time, "monotonic", lambda: now[0])
    states = AgentStates(["a", "b"])
    states.enter("a", AgentState.WORKING)
    now[0] += 2
    states.enter("b", AgentState.MODEL_REQUEST)
    now[0] += 3
    assert states.counts()[AgentState.WORKING] == 1
    seconds = states.agent_seconds()
    assert seconds[AgentState.WORKING] == 5
    assert seconds[AgentState.MODEL_REQUEST] == 3
    assert seconds[AgentState.PENDING] == 2
    assert states.by_category() == {"working": 5, "waiting": 0, "model_request": 3}


async def test_activities_nest_and_overlap():
    states = AgentStates(["a"])

    async def agent():
        _agents.set_current_agent("a", states)
        with _agents.activity(AgentState.TOOL_WAIT):
            assert states.state("a") == AgentState.TOOL_WAIT
            with _agents.activity(AgentState.TOOL_CALL):  # a second call, at once
                assert states.state("a") == AgentState.TOOL_CALL
            assert states.state("a") == AgentState.TOOL_WAIT
        assert states.state("a") == AgentState.WORKING

    await asyncio.create_task(agent())
    assert _agents.current_agent() is None  # set in the agent's task only


def test_activity_outside_a_host_does_nothing():
    with _agents.activity(AgentState.MODEL_REQUEST):
        assert _agents.current_agent() is None


class FakeRunner:
    """Each agent sleeps; agents named ``s*`` suspend once on a task, ``x*``
    raise, ``h*`` stop on a foreign interrupt."""

    def __init__(self, sleep: float = 0.01):
        self.sleep = sleep
        self.live = 0
        self.most = 0
        self.starts: list[float] = []

    async def start(self, agent):
        self.live += 1
        self.most = max(self.most, self.live)
        self.starts.append(asyncio.get_running_loop().time())
        try:
            await asyncio.sleep(self.sleep)
            if agent.id.startswith("x"):
                raise ValueError("bad agent")
            if agent.id.startswith("h"):
                return Interrupted(("ask a human",))
            if agent.id.startswith("s"):
                future = asyncio.get_running_loop().create_future()
                asyncio.get_running_loop().call_later(0.05, future.set_result, 7)
                register_pending(f"task-{agent.id}", future)
                return Suspended((("i1", f"task-{agent.id}"),))
            return Finished({"agent": agent.id})
        finally:
            self.live -= 1

    async def resume(self, agent, done):
        assert done == {"i1": {"flowgentic_done": f"task-{agent.id}"}}
        return Finished({"agent": agent.id, "resumed": True})


async def test_live_agents_stay_under_the_cap():
    runner = FakeRunner()
    host = AgentHost(runner, max_live_agents=10, stagger=FAST, collector=None)
    report = await host.run(Agent(f"a{i}", None) for i in range(50))
    assert report.finished == 50
    assert runner.most <= 10


async def test_starts_are_staggered():
    runner = FakeRunner(sleep=0.3)
    host = AgentHost(runner, stagger=Stagger(batch=5, interval_s=0.1), collector=None)
    await host.run(Agent(f"a{i}", None) for i in range(15))
    t = sorted(runner.starts)
    assert t[4] - t[0] < 0.05  # one batch together
    assert t[5] - t[4] > 0.08 and t[10] - t[9] > 0.08  # batches apart


async def test_collector_is_tuned_during_a_run_and_restored():
    before = gc.get_threshold()
    already_frozen = bool(gc.get_freeze_count())
    seen = {}

    class Probe(FakeRunner):
        async def start(self, agent):
            await asyncio.sleep(0.05)
            seen["threshold"] = gc.get_threshold()[0]
            seen["frozen"] = gc.get_freeze_count()
            return Finished(None)

    report = await AgentHost(Probe(), stagger=FAST).run([Agent("a", None)])
    assert seen["threshold"] == 100_000 and seen["frozen"] > 0
    assert gc.get_threshold() == before and bool(gc.get_freeze_count()) == already_frozen
    assert report.collector["freeze"]["frozen"] > 0
    assert report.collector["freeze"].get("already_frozen", False) == already_frozen


async def test_collector_is_restored_when_a_run_is_cancelled():
    before = gc.get_threshold()
    already_frozen = bool(gc.get_freeze_count())
    host = AgentHost(FakeRunner(sleep=10), stagger=FAST)
    task = asyncio.create_task(host.run([Agent("a", None)]))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gc.get_threshold() == before and bool(gc.get_freeze_count()) == already_frozen


async def test_suspend_failure_and_foreign_interrupt():
    outputs = {}
    host = AgentHost(
        FakeRunner(),
        stagger=FAST,
        collector=None,
        on_done=lambda result, output: outputs.__setitem__(result.id, output),
    )
    states_seen = []

    async def watch():
        for _ in range(20):
            states_seen.append(host.states.state("s1"))
            await asyncio.sleep(0.005)

    report, _ = await asyncio.gather(
        host.run([Agent("s1", None), Agent("x1", None), Agent("h1", None), Agent("a1", None)]),
        watch(),
    )
    by_id = {a.id: a for a in report.agents}
    assert by_id["s1"].status == "done" and by_id["s1"].suspensions == 1
    assert outputs["s1"] == {"agent": "s1", "resumed": True}
    assert AgentState.TOOL_WAIT in states_seen
    assert by_id["x1"].status == "failed" and "bad agent" in by_id["x1"].error
    assert by_id["h1"].status == "interrupted" and by_id["h1"].interrupts == ("ask a human",)
    assert by_id["a1"].status == "done"
    assert report.errors == {"ValueError": 1}
    assert set(host.states.counts().items()) >= {(AgentState.DONE, 4)}
    assert report.loop_lag["samples"] >= 0


async def test_a_task_this_process_never_submitted_fails_the_agent():
    class Lost(FakeRunner):
        async def start(self, agent):
            return Suspended((("i1", "nowhere"),))

    report = await AgentHost(Lost(), stagger=FAST, collector=None).run([Agent("a", None)])
    assert report.agents[0].status == "failed"
    assert "did not submit" in report.agents[0].error


def points(reader, name: str) -> list:
    """The data points of metric ``name`` in the reader's latest collection."""
    data = reader.get_metrics_data()
    out = []
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == name:
                    out.extend(metric.data.data_points)
    return out


async def test_the_host_records_agents_spans_and_its_own_health(
    span_exporter, log_exporter, metric_reader
):
    runner = FakeRunner(sleep=0.25)  # long enough for a few 100 ms lag samples
    host = AgentHost(runner, stagger=FAST)
    await host.run([Agent("s1", None), Agent("a1", None)])

    spans = {s.name: s for s in span_exporter.get_finished_spans()}
    assert {"invoke_agent s1", "invoke_agent a1"} <= set(spans)
    assert spans["invoke_agent s1"].attributes["gen_ai.agent.id"] == "s1"
    assert spans["invoke_agent s1"].attributes["gen_ai.operation.name"] == "invoke_agent"
    events = [r.log_record for r in log_exporter.get_finished_logs()]
    suspends = [e for e in events if e.event_name == "flowgentic.agent.suspend"]
    assert [e.attributes["gen_ai.agent.id"] for e in suspends] == ["s1"]
    assert suspends[0].attributes["flowgentic.agent.suspend.waits"] == 1

    done = [p for p in points(metric_reader, "flowgentic.agents") if p.attributes["flowgentic.agent.state"] == "done"]
    assert done and done[0].value >= 2
    time_by_state = {
        p.attributes["flowgentic.agent.state"]: p for p in points(metric_reader, "flowgentic.agent.time")
    }
    assert time_by_state["tool_wait"].attributes["flowgentic.agent.category"] == "waiting"
    assert time_by_state["tool_wait"].value > 0
    assert points(metric_reader, "flowgentic.host.loop.lag")[0].count > 0


async def test_duplicate_ids_fail_before_any_agent_starts():
    runner = FakeRunner()
    host = AgentHost(runner, collector=None)
    with pytest.raises(ValueError, match="unique"):
        await host.run([Agent("same", 1), Agent("same", 2)])
    assert not runner.starts


async def test_one_cancelled_agent_and_callback_failure_leave_peers_running():
    class Runner(FakeRunner):
        async def start(self, agent):
            register_pending(agent.id, asyncio.get_running_loop().create_future())
            if agent.id == "cancel":
                raise asyncio.CancelledError()
            return Finished(None)

    def on_done(result, _output):
        if result.id == "callback":
            raise ValueError("callback failed")

    host = AgentHost(Runner(), on_done=on_done, collector=None)
    report = await host.run([Agent(key, None) for key in ("cancel", "callback", "ok")])
    assert report.finished == 1 and report.errors == {"CancelledError": 1, "ValueError": 1}
    assert all(_agents.pending(result.id) is None for result in report.agents)
    await host.run([Agent("again", None)])
    assert host.states.counts()[AgentState.DONE] == 1


async def test_overlapping_hosts_restore_process_collector():
    before = gc.get_threshold()
    already_frozen = bool(gc.get_freeze_count())
    first, second = asyncio.Event(), asyncio.Event()

    class Waiting:
        async def start(self, agent):
            await agent.input.wait()
            return Finished(None)

    async def launch(key, threshold, event):
        host = AgentHost(Waiting(), collector=Collector(threshold=threshold))
        task = asyncio.create_task(host.run([Agent(key, event)]))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return task

    a = await launch("a", 100_001, first)
    b = await launch("b", 100_002, second)
    try:
        first.set()
        await a
        assert gc.get_threshold()[0] == 100_002 and gc.get_freeze_count() > 0
    finally:
        second.set()
        await b
    assert gc.get_threshold() == before and bool(gc.get_freeze_count()) == already_frozen


def test_host_preserves_a_preexisting_frozen_generation():
    # Freezing then unfreezing in pytest would also thaw interpreter-owned objects.
    code = """
import asyncio, gc
from flowgentic.host import Agent, AgentHost, Finished

class Runner:
    async def start(self, agent):
        return Finished(None)

gc.freeze()
report = asyncio.run(AgentHost(Runner()).run([Agent("a", None)]))
assert report.collector["freeze"]["already_frozen"]
assert gc.get_freeze_count() > 0
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_pending_seconds_start_when_the_agent_is_added(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(_agents.time, "monotonic", lambda: now[0])
    states = AgentStates()
    now[0] += 10
    states.add("a")
    now[0] += 2
    assert states.agent_seconds()[AgentState.PENDING] == 2


async def test_indexed_run_bounds_inputs_and_retains_only_aggregate_outcomes():
    built, completed = [], []
    host = None

    def make_input(i):
        built.append(i)
        assert len(built) - len(completed) <= 3
        return i

    class Runner:
        async def start(self, agent):
            assert agent.id == f"job-{agent.input}"
            assert len(host.states._state) <= 3
            await asyncio.sleep(0)
            if agent.input == 12:
                raise ValueError("bad")
            if agent.input == 14:
                return Interrupted(("ask",))
            return Finished(agent.input)

    host = AgentHost(Runner(), max_live_agents=3, stagger=FAST, collector=None,
                     on_done=lambda result, output: completed.append(result.id))
    report = await host.run_indexed(range(10, 30, 2), make_input, id_prefix="job-")
    assert sorted(completed) == [f"job-{i}" for i in range(10, 30, 2)]
    assert report.agents == [] and report.finished == 8
    assert report.outcomes == {"done": 8, "failed": 1, "interrupted": 1}
    assert report.errors == {"ValueError": 1}
    assert host.states.counts()[AgentState.DONE] == 10
    assert not host.states._state


async def test_indexed_input_failure_cancels_live_tasks_and_releases_host():
    started, stopped = asyncio.Event(), asyncio.Event()

    class Runner:
        async def start(self, agent):
            if agent.id == "0":
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()
            return Finished(None)

    def inputs(i):
        if i == 2:
            raise ValueError("input failure")

    host = AgentHost(Runner(), max_live_agents=2, collector=None, stagger=FAST)
    with pytest.raises(ValueError, match="input failure"):
        await host.run_indexed(range(3), inputs)
    assert started.is_set() and stopped.is_set()
    assert (await host.run_indexed(range(0), inputs)).finished == 0
