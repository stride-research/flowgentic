"""Task tools on the application's AsyncFlow engine, and the decision record.

A ``ToolRuntime`` given an engine runs each task tool as an AsyncFlow function
task on a named backend of that engine, beside the RHAPSODY path: repeat safety,
suspension and the host's registrations work as they do on a session. Each
task's ``TaskRecord`` ties it to the agent and the tool call that asked for it,
with its uid and the times of commitment, submission, start and end."""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from _support import RUNS, Scripted, broken, calls_then_answer, count, react_graph, simulate, thread_session
from langchain_core.messages import HumanMessage

from flowgentic import _agents, _asyncflow
from flowgentic.fleet import Fleet
from flowgentic.host import Agent, AgentHost, Finished, Stagger
from flowgentic.langgraph import GraphRunner, KeyValueSaver, bind_tools
from flowgentic.store import MemoryStore
from flowgentic.tools import TaskCancelled, ToolRuntime, task_tool

FAST = Stagger(batch=50, interval_s=0.0)


@pytest.fixture(autouse=True)
def clear_runs():
    RUNS.clear()


async def open_engine(*names: str, workers: int = 4):
    """An AsyncFlow engine on RHAPSODY thread pools, one per name, the first
    the default. Each pool's threads are named after its backend."""
    from rhapsody.backends import ConcurrentExecutionBackend

    backends = [
        await ConcurrentExecutionBackend(
            ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"pool-{name}"), name=name
        )
        for name in names
    ]
    return await _asyncflow.open_engine(backends)


def inputs(n: int, prefix: str = "planner"):
    return [Agent(f"{prefix}-{i}", {"messages": [HumanMessage("go")]}) for i in range(n)]


def where(x: float) -> dict:
    """Run x, and say which pool ran it."""
    count("where")
    return {"y": 2 * x, "thread": threading.current_thread().name}


async def where_async(x: float) -> dict:
    """Run x asynchronously, and say which pool ran it."""
    await asyncio.sleep(0)
    return where(x)


def echo(task_description: str, workflow_id: str) -> dict:
    """Arguments named as AsyncFlow's own call keywords."""
    return {"task_description": task_description, "workflow_id": workflow_id}


async def _half(x: float) -> float:
    await asyncio.sleep(0)
    return x / 2


def nested(x: float) -> dict:
    """A sync function that runs an async library of its own, as a sync client
    of one does."""
    return {"y": asyncio.run(_half(x))}


def slow(x: float) -> dict:
    """An action that takes a while."""
    count("slow")
    time.sleep(0.3)
    return {"y": 2 * x}


GATE = threading.Event()


def held(x: float) -> dict:
    """Run until the test opens the gate."""
    GATE.wait(5)
    return {"y": 2 * x}


def tasks_running(flow) -> list[str]:
    """The uids of the engine's tasks running on a backend, blocks left out."""
    return sorted(uid for uid in flow.running if uid.startswith("task."))


async def record_while_running(flow, submit) -> dict:
    """Call ``submit()``, which asks for one ``held`` task, and return AsyncFlow's
    record of the task, read while the task runs: AsyncFlow may release a
    finished task's record. Then let the task end."""
    GATE.clear()
    before = set(tasks_running(flow))  # a task just ended can linger there
    pending = submit()
    try:
        await until(lambda: set(tasks_running(flow)) - before)
        (uid,) = set(tasks_running(flow)) - before
        return {"uid": uid, **flow.components[uid]["description"]}
    finally:
        GATE.set()
        await asyncio.wait([asyncio.ensure_future(pending)])


async def until(condition, timeout: float = 5.0) -> None:
    """Wait until ``condition()`` holds, failing after ``timeout`` seconds."""
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end, "condition not reached"
        await asyncio.sleep(0.01)


# --- where the task runs -----------------------------------------------------------


async def test_a_task_tool_runs_on_the_named_backend_of_the_applications_engine():
    flow = await open_engine("ai", "compute")
    runtime = ToolRuntime(engine=flow, backend="compute")
    records = []
    runtime.add_listener(records.append)
    try:
        value = await runtime.submit(task_tool(where), "planner-1", "call-1", {"x": 1.5})
        own = await runtime.submit(task_tool(where, backend="ai"), "planner-1", "call-2", {"x": 2.0})
        first, second = records
        # AsyncFlow knows the task by the function's name, under the record's uid
        named = await record_while_running(
            flow, lambda: runtime.submit(task_tool(held), "planner-1", "call-3", {"x": 1.0})
        )
    finally:
        await _asyncflow.close_engine(flow)
    assert value["y"] == 3.0 and value["thread"].startswith("pool-compute")
    assert own["thread"].startswith("pool-ai")  # a tool's own backend wins
    assert first.backend == "compute" and second.backend == "ai"
    assert first.uid.startswith("task.") and first.uid != second.uid
    assert (first.info.agent_id, first.info.call_id) == ("planner-1", "call-1")
    assert first.ok and first.value["y"] == 3.0
    assert named["name"] == "held" and named["uid"] == records[2].uid
    assert RUNS == {"where": 2}


async def test_without_a_backend_named_a_task_runs_on_the_engines_default():
    flow = await open_engine("ai", "compute")
    runtime = ToolRuntime(engine=flow)
    records = []
    runtime.add_listener(records.append)
    try:
        value = await runtime.submit(task_tool(where), "planner-1", "call-1", {"x": 1.0})
    finally:
        await _asyncflow.close_engine(flow)
    assert value["thread"].startswith("pool-ai")
    assert records[0].backend == "ai"


async def test_a_tool_with_backend_none_runs_where_the_runtime_says():
    """``backend=None`` on a tool names no backend: the runtime's applies, else
    the engine's or the session's default."""
    flow = await open_engine("ai", "compute")
    session = await thread_session()
    runtime = ToolRuntime(engine=flow, backend="compute")
    on_session = ToolRuntime(session)
    records = []
    runtime.add_listener(records.append)
    on_session.add_listener(records.append)
    try:
        bind_tools(runtime, [task_tool(where, backend=None)])
        bind_tools(on_session, [task_tool(simulate, backend=None)])
        value = await runtime.submit(task_tool(where, backend=None), "a", "c1", {"x": 1.0})
        await on_session.submit(task_tool(simulate, backend=None), "a", "c2", {"x": 1.0})
    finally:
        await _asyncflow.close_engine(flow)
        await session.close()
    assert value["thread"].startswith("pool-compute")
    assert [r.backend for r in records] == ["compute", "concurrent"]


async def test_sync_and_async_functions_run_with_any_argument_names():
    """AsyncFlow reads some keywords of a call as its own; a tool's arguments
    reach its function whatever their names."""
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    try:
        value = await runtime.submit(task_tool(where_async), "a", "c1", {"x": 1.0})
        named = await runtime.submit(
            task_tool(echo), "a", "c2", {"task_description": "plain text", "workflow_id": "mine"}
        )
    finally:
        await _asyncflow.close_engine(flow)
    assert value["y"] == 2.0 and value["thread"].startswith("pool-compute")
    assert named == {"task_description": "plain text", "workflow_id": "mine"}


async def test_a_sync_function_runs_with_no_event_loop_running_as_on_a_session():
    """AsyncFlow runs only ``async def`` tasks, so the runtime wraps a sync
    function in one; the function itself must still be called as on a
    session, with no event loop running in its thread."""
    flow = await open_engine("compute")
    session = await thread_session()
    try:
        on_engine = await ToolRuntime(engine=flow).submit(task_tool(nested), "a", "c1", {"x": 3.0})
        on_session = await ToolRuntime(session).submit(task_tool(nested), "a", "c1", {"x": 3.0})
    finally:
        await _asyncflow.close_engine(flow)
        await session.close()
    assert on_engine == on_session == {"y": 1.5}


async def test_the_task_runs_in_its_callers_trace_context(span_exporter):
    from opentelemetry import trace

    def traced(x: float) -> dict:
        return {"trace": trace.get_current_span().get_span_context().trace_id}

    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    try:
        value = await runtime.submit(task_tool(traced), "a", "c1", {"x": 1.0})
    finally:
        await _asyncflow.close_engine(flow)
    (span,) = [s for s in span_exporter.get_finished_spans() if s.name == "execute_tool traced"]
    assert value["trace"] == span.context.trace_id


def where_process(x: float) -> dict:
    """Run x, and say which process ran it."""
    import os

    return {"y": 2 * x, "pid": os.getpid()}


async def test_a_task_runs_on_a_process_pool():
    """A process backend pickles the task's function, with the runtime's wrapper
    around it; a failure comes back pickled too."""
    import os
    from concurrent.futures import ProcessPoolExecutor

    from rhapsody.backends import ConcurrentExecutionBackend

    backend = await ConcurrentExecutionBackend(ProcessPoolExecutor(max_workers=1), name="processes")
    flow = await _asyncflow.open_engine(backend)
    runtime = ToolRuntime(engine=flow)
    try:
        value = await runtime.submit(task_tool(where_process), "a", "c1", {"x": 1.0})
        failed = runtime.submit(task_tool(broken), "a", "c2", {"x": 1.5})
        await asyncio.wait([failed])
    finally:
        await _asyncflow.close_engine(flow)
    assert value["y"] == 2.0 and value["pid"] != os.getpid()
    assert "no convergence at 1.5" in str(failed.exception())


# --- repeat safety, suspension, failure and cancellation ------------------------


async def test_the_same_agent_and_call_get_the_first_task():
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    records = []
    runtime.add_listener(records.append)
    try:
        first = runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
        again = runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
        assert first is again
        assert await first == {"y": 2.0}
        # Once the task has ended, the runtime keeps its value, not the task.
        later = runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
        assert later is not first and later.done() and later.result() == {"y": 2.0}
        other = await runtime.submit(task_tool(simulate), "b", "c1", {"x": 1.0})
    finally:
        await _asyncflow.close_engine(flow)
    assert other == {"y": 2.0}
    assert RUNS == {"simulate": 2}
    assert runtime.stats()["repeats"] == 2 and runtime.stats()["submitted"] == 2
    assert len(records) == 2


async def test_suspended_planners_resume_and_each_action_runs_once():
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    graph = react_graph(
        Scripted(script=calls_then_answer(("simulate", {"x": 1.5}))),
        bind_tools(runtime, [task_tool(simulate, suspend=True)]),
        KeyValueSaver(MemoryStore()),
    )
    outputs = {}
    host = AgentHost(GraphRunner(graph), stagger=FAST, on_done=lambda r, out: outputs.__setitem__(r.id, out))
    try:
        report = await host.run(inputs(20))
    finally:
        await _asyncflow.close_engine(flow)
    assert report.finished == 20
    assert all(a.suspensions == 1 for a in report.agents)
    assert RUNS == {"simulate": 20}  # each ran once per agent
    assert runtime.stats()["repeats"] == 20  # the resume found the task
    assert outputs["planner-0"]["messages"][-1].content == '{"y": 3.0}'
    # the host holds no task of an agent that has ended, and the runtime keeps
    # each one's outcome, not the task
    assert runtime._jobs == {} and runtime.stats()["retained"] == 20
    assert all(_agents.pending(key) is None for key in runtime._ended)


async def test_a_task_asked_for_in_an_agent_is_registered_for_the_host():
    """``register_pending`` works for a task on an engine as for one on a
    session: a task submitted inside an agent is registered for the host."""
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    seen = {}

    class Runner:
        async def start(self, agent):
            from flowgentic.host import Suspended

            runtime.submit(task_tool(simulate), agent.id, "c1", {"x": 1.0})
            key = runtime.key(agent.id, "c1")
            seen["registered"] = _agents.registered(key) is runtime._jobs[key]
            return Suspended(((f"r-{agent.id}", key),))

        async def resume(self, agent, done):
            from flowgentic.host import Finished

            return Finished(await runtime.submit(task_tool(simulate), agent.id, "c1", {"x": 1.0}))

    outputs = {}
    try:
        report = await AgentHost(
            Runner(), stagger=FAST, on_done=lambda r, out: outputs.__setitem__(r.id, out)
        ).run(inputs(1))
    finally:
        await _asyncflow.close_engine(flow)
    assert report.finished == 1 and seen == {"registered": True}
    assert outputs == {"planner-0": {"y": 2.0}} and RUNS == {"simulate": 1}


async def test_a_failed_task_reaches_the_model_as_a_tool_error():
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    records = []
    runtime.add_listener(records.append)
    graph = react_graph(
        Scripted(script=calls_then_answer(("broken", {"x": 1.5}))),
        bind_tools(runtime, [task_tool(broken, suspend=True)]),
        KeyValueSaver(MemoryStore()),
    )
    outputs = {}
    host = AgentHost(GraphRunner(graph), stagger=FAST, on_done=lambda r, out: outputs.__setitem__(r.id, out))
    try:
        report = await host.run(inputs(1))
    finally:
        await _asyncflow.close_engine(flow)
    assert report.finished == 1
    assert "broken failed: ValueError: no convergence at 1.5" in outputs["planner-0"]["messages"][-1].content
    assert runtime.stats()["failed"] == 1
    (record,) = records
    assert record.outcome == "error" and "no convergence at 1.5" in record.error


async def test_a_task_ends_cancelled_when_the_engine_shuts_down():
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    records = []
    runtime.add_listener(records.append)
    job = runtime.submit(task_tool(slow), "a", "c1", {"x": 1.0})
    await until(lambda: records or flow.running)
    await _asyncflow.close_engine(flow)
    await asyncio.wait([job])
    assert isinstance(job.exception(), TaskCancelled)
    # and a task asked for once the engine has shut down never runs
    late = runtime.submit(task_tool(simulate), "a", "c2", {"x": 1.0})
    await asyncio.wait([late])
    assert isinstance(late.exception(), TaskCancelled)
    assert [r.outcome for r in records] == ["cancelled", "cancelled"]
    assert "simulate" not in RUNS


async def test_cancelling_the_task_cancels_it_on_the_engine():
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    records = []
    runtime.add_listener(records.append)
    try:
        job = runtime.submit(task_tool(slow), "a", "c1", {"x": 1.0})
        await until(lambda: flow.running)
        (uid,) = flow.running
        future = flow.components[uid]["future"]
        job.cancel()
        await asyncio.wait([job])
        await until(future.done)
        assert future.cancelled()
    finally:
        await _asyncflow.close_engine(flow)
    (record,) = records
    assert record.outcome == "cancelled" and record.uid == uid


async def test_a_task_asked_for_inside_a_block_is_cancelled_with_the_block():
    """An agent that runs inside an AsyncFlow block asks for tasks that belong
    to the block: cancelling the block cancels them, as it does the block's
    own tasks, though the tool's wait alone leaves a started task running."""
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    records = []
    runtime.add_listener(records.append)
    (tool_,) = bind_tools(runtime, [task_tool(slow)])
    call = {"name": "slow", "args": {"x": 1.0}, "id": "c1", "type": "tool_call"}

    async def planner():
        return await tool_.ainvoke(call, {"configurable": {"thread_id": "planner-1"}})

    try:
        block = _asyncflow.block(flow, planner)()
        await until(lambda: tasks_running(flow))
        (uid,) = tasks_running(flow)
        task = flow.components[uid]["future"]
        block.cancel()
        await until(task.done)
        assert task.cancelled()
        await until(lambda: records)
    finally:
        await _asyncflow.close_engine(flow)
    (record,) = records
    assert record.uid == uid and record.outcome == "cancelled"
    assert record.error == "TaskCancelled: AsyncFlow cancelled the task"


async def test_the_tasks_of_a_host_run_inside_a_block_are_cancelled_with_the_block():
    """The same for a population: an agent host run as a block, its agents
    suspended on their tasks."""
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    records = []
    runtime.add_listener(records.append)
    graph = react_graph(
        Scripted(script=calls_then_answer(("slow", {"x": 1.0}))),
        bind_tools(runtime, [task_tool(slow, suspend=True)]),
        KeyValueSaver(MemoryStore()),
    )

    async def population():
        return await AgentHost(GraphRunner(graph), stagger=FAST).run(inputs(3))

    try:
        block = _asyncflow.block(flow, population)()
        await until(lambda: len(tasks_running(flow)) == 3)
        tasks = [flow.components[uid]["future"] for uid in tasks_running(flow)]
        block.cancel()
        await until(lambda: all(task.done() for task in tasks))
        assert all(task.cancelled() for task in tasks)
        await until(lambda: len(records) == 3)
    finally:
        await _asyncflow.close_engine(flow)
    assert [r.outcome for r in records] == ["cancelled"] * 3


async def test_a_task_carries_the_workflow_it_was_asked_for_in():
    """AsyncFlow labels each task with the workflow scope or block it is
    called in, and its telemetry reports the label: a task an agent asks for
    gets the agent's."""
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    records = []
    runtime.add_listener(records.append)

    async def in_block():
        return await runtime.submit(task_tool(held), "a", "c2", {"x": 1.0})

    blocks = []

    def run_block():
        blocks.append(_asyncflow.block(flow, in_block)())
        return blocks[-1]

    try:
        async with flow.workflow_scope("campaign-1"):
            scoped = await record_while_running(
                flow, lambda: runtime.submit(task_tool(held), "a", "c1", {"x": 1.0})
            )
        blocked = await record_while_running(flow, run_block)
        outside = await record_while_running(
            flow, lambda: runtime.submit(task_tool(held), "a", "c3", {"x": 1.0})
        )
    finally:
        await _asyncflow.close_engine(flow)
    assert [r["workflow_id"] for r in (scoped, blocked, outside)] == [
        "campaign-1",
        blocks[0].uid,
        None,
    ]
    assert [r.uid for r in records] == [scoped["uid"], blocked["uid"], outside["uid"]]


async def test_a_task_tool_called_off_the_engines_event_loop_fails_its_agent():
    """AsyncFlow registers a task on the engine's own event loop. An agent that
    runs inside an AsyncFlow task, as ``bind_agent`` runs one, has a loop of its
    own in a pool thread: its task tool raises ``RuntimeError``, which fails the
    agent, rather than wait forever for a task the engine never registers."""
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)

    def in_a_thread_of_its_own():
        async def agent():
            return runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})

        return asyncio.run(agent())

    try:
        with pytest.raises(RuntimeError, match="event loop"):
            await asyncio.wait_for(asyncio.to_thread(in_a_thread_of_its_own), 5)
        # nothing was submitted, and the engine still runs tasks
        assert runtime._jobs == {} and runtime.stats()["submitted"] == 0
        assert await runtime.submit(task_tool(simulate), "a", "c2", {"x": 1.0}) == {"y": 2.0}
    finally:
        await asyncio.wait_for(_asyncflow.close_engine(flow), 10)


async def test_an_asyncflow_without_the_scope_it_reads_is_refused_by_version(monkeypatch):
    """The seam reads one private name of AsyncFlow's, the engine's scope
    variable. An AsyncFlow without it is refused when the runtime is made, with
    an error that names the installed version, not later with an
    ``AttributeError``."""
    from importlib.metadata import version

    flow = await open_engine("compute")
    try:
        monkeypatch.delattr(flow, "_workflow_id_ctx")
        with pytest.raises(RuntimeError, match=f"radical-asyncflow {version('radical-asyncflow')}"):
            ToolRuntime(engine=flow)
        monkeypatch.undo()
        monkeypatch.setattr(flow, "_workflow_id_ctx", "not a context variable")
        with pytest.raises(RuntimeError, match="radical-asyncflow"):
            ToolRuntime(engine=flow)
    finally:
        monkeypatch.undo()
        await _asyncflow.close_engine(flow)


async def test_another_engines_telemetry_is_refused():
    """An engine emits its events to its own telemetry only: with another
    engine's, no record would get its start."""
    pytest.importorskip("rhapsody.telemetry.manager", reason="AsyncFlow telemetry needs rhapsody-py[telemetry]")
    first, second = await open_engine("compute"), await open_engine("compute")
    own, other = await first.start_telemetry(), await second.start_telemetry()
    try:
        with pytest.raises(ValueError, match="another engine"):
            ToolRuntime(engine=first, telemetry=other)
        assert ToolRuntime(engine=first, telemetry=own).telemetry is own
    finally:
        await own.stop()
        await other.stop()
        await _asyncflow.close_engine(first)
        await _asyncflow.close_engine(second)


async def test_a_runtime_set_up_wrong_is_refused_before_any_agent_runs():
    """A backend the engine or session lacks, or a field an engine cannot take,
    is refused when the runtime is made or its tools are bound, not told to a
    model at the tool's first call. A tool submitted unbound fails as a runtime
    set up wrong does: with ``RuntimeError``, which fails its agent."""
    flow = await open_engine("compute")
    session = await thread_session()
    try:
        with pytest.raises(ValueError, match="not both"):
            ToolRuntime(session, engine=flow)
        with pytest.raises(ValueError, match="Unknown backend 'gpu'"):
            ToolRuntime(engine=flow, backend="gpu")
        with pytest.raises(ValueError, match="no backend 'gpu'"):
            ToolRuntime(session, backend="gpu")
        with pytest.raises(ValueError, match="telemetry"):
            ToolRuntime(session, telemetry=object())
        runtime = ToolRuntime(engine=flow)
        on_session = ToolRuntime(session)
        with pytest.raises(ValueError, match="Unknown backend 'gpu'"):
            bind_tools(runtime, [task_tool(simulate, backend="gpu")])
        with pytest.raises(ValueError, match="ranks"):
            bind_tools(runtime, [task_tool(simulate, ranks=4)])
        with pytest.raises(ValueError, match="no backend 'gpu'"):
            bind_tools(on_session, [task_tool(simulate, backend="gpu")])
        for wrong in (runtime, on_session):
            with pytest.raises(ValueError, match="task_backend_specific_kwargs"):
                bind_tools(wrong, [task_tool(simulate, task_backend_specific_kwargs="cpus=4")])
        with pytest.raises(RuntimeError, match="Unknown backend 'gpu'"):
            runtime.submit(task_tool(simulate, backend="gpu"), "a", "c1", {"x": 1.0})
        with pytest.raises(RuntimeError, match="ranks"):
            runtime.submit(task_tool(simulate, ranks=4), "a", "c2", {"x": 1.0})
        with pytest.raises(RuntimeError, match="no backend 'gpu'"):
            on_session.submit(task_tool(simulate, backend="gpu"), "a", "c3", {"x": 1.0})
        # nothing was submitted, so the calls made again would run afresh
        assert runtime._jobs == {} and runtime.stats()["submitted"] == 0
        assert on_session._jobs == {} and on_session.stats()["submitted"] == 0
    finally:
        await _asyncflow.close_engine(flow)
        await session.close()


async def test_backend_specific_fields_reach_the_backend():
    """``task_backend_specific_kwargs`` means the same on both paths: AsyncFlow
    passes it to the backend as the task's description."""
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    records = []
    runtime.add_listener(records.append)
    try:
        seen = await record_while_running(
            flow,
            lambda: runtime.submit(
                task_tool(held, task_backend_specific_kwargs={"priority": 3}), "a", "c1", {"x": 1.0}
            ),
        )
    finally:
        await _asyncflow.close_engine(flow)
    assert seen["task_backend_specific_kwargs"] == {"priority": 3}
    assert seen["uid"] == records[0].uid


async def test_a_placement_chooses_each_calls_backend_and_its_fields():
    """On an engine, a placement can name the backend, and the backend's own
    fields, per call: a call with a large x runs on "ai"."""
    flow = await open_engine("compute", "ai")
    seen_on = {}

    def place(info):
        if info.args["x"] > 1:
            return {"backend": "ai", "task_backend_specific_kwargs": {"priority": 3}}
        return None

    runtime = ToolRuntime(engine=flow, place=place)
    records = []
    runtime.add_listener(records.append)
    try:
        small = await runtime.submit(task_tool(where), "a", "c1", {"x": 1.0})
        seen_on = await record_while_running(
            flow, lambda: runtime.submit(task_tool(held), "a", "c2", {"x": 2.0})
        )
        large = await runtime.submit(task_tool(where), "a", "c3", {"x": 2.0})
    finally:
        await _asyncflow.close_engine(flow)
    assert small["thread"].startswith("pool-compute") and large["thread"].startswith("pool-ai")
    assert seen_on["task_backend_specific_kwargs"] == {"priority": 3}
    assert [r.backend for r in records] == ["compute", "ai", "ai"]


# --- the decision record ---------------------------------------------------------


async def test_the_record_takes_its_start_from_the_telemetry_the_application_passes():
    pytest.importorskip("rhapsody.telemetry.manager", reason="AsyncFlow telemetry needs rhapsody-py[telemetry]")
    flow = await open_engine("compute")
    telemetry = await flow.start_telemetry()
    events = {}
    telemetry.subscribe(lambda e: events.setdefault((e.task_id, e.event_type), e))
    runtime = ToolRuntime(engine=flow, telemetry=telemetry)
    records = []
    runtime.add_listener(records.append)
    try:
        await runtime.submit(task_tool(simulate), "planner-1", "call-1", {"x": 1.0})
        failed = runtime.submit(task_tool(broken), "planner-1", "call-2", {"x": 1.0})
        await asyncio.wait([failed])
    finally:
        await telemetry.stop()
        await _asyncflow.close_engine(flow)
    ok, no = records
    assert ok.t_start == events[(ok.uid, "TaskStarted")].event_time
    assert no.t_start == events[(no.uid, "TaskStarted")].event_time
    # the finish is when the agent's process saw AsyncFlow resolve the task:
    # just after AsyncFlow's own end event
    assert events[(ok.uid, "TaskCompleted")].event_time <= ok.t_finish
    assert events[(no.uid, "TaskFailed")].event_time <= no.t_finish
    # the events name the backend the record names
    assert events[(ok.uid, "TaskStarted")].backend == ok.backend == "compute"
    for r in (ok, no):
        assert r.t_commit == r.t_submit <= r.t_admit <= r.t_start <= r.t_finish <= r.t_end


async def test_every_record_has_its_finish_in_a_burst_of_tasks():
    """A burst of tasks floods the telemetry's queue, which delivers events in
    batches: an end event can reach the runtime well after the task's record
    is made. The finish does not depend on it, and a start that came too late
    for the record is in the telemetry under the record's uid."""
    pytest.importorskip("rhapsody.telemetry.manager", reason="AsyncFlow telemetry needs rhapsody-py[telemetry]")
    flow = await open_engine("compute", workers=32)
    telemetry = await flow.start_telemetry()
    started, ended = {}, {}

    def seen(event):
        if event.event_type == "TaskStarted":
            started.setdefault(event.task_id, event.event_time)
        elif event.event_type == "TaskCompleted":
            ended.setdefault(event.task_id, event.event_time)

    telemetry.subscribe(seen)
    runtime = ToolRuntime(engine=flow, telemetry=telemetry)
    records = []
    runtime.add_listener(records.append)
    try:
        jobs = [runtime.submit(task_tool(simulate), "a", f"c{i}", {"x": float(i)}) for i in range(1000)]
        await asyncio.wait(jobs)
    finally:
        await telemetry.stop()  # delivers every event still queued
        await _asyncflow.close_engine(flow)
    assert len(records) == 1000 and all(r.ok for r in records)
    assert all(r.t_finish is not None for r in records)
    for r in records:
        assert ended[r.uid] <= r.t_finish <= r.t_end
        assert r.t_start in (None, started[r.uid])
    assert all(r.t_start is not None or r.uid in started for r in records)


async def test_without_the_telemetry_the_record_has_no_start():
    """The runtime reads only the telemetry the application gives it."""
    pytest.importorskip("rhapsody.telemetry.manager", reason="AsyncFlow telemetry needs rhapsody-py[telemetry]")
    flow = await open_engine("compute")
    telemetry = await flow.start_telemetry()  # started, but not given
    runtime = ToolRuntime(engine=flow)
    records = []
    runtime.add_listener(records.append)
    try:
        await runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
    finally:
        await telemetry.stop()
        await _asyncflow.close_engine(flow)
    (record,) = records
    assert record.uid.startswith("task.") and record.t_start is None
    assert record.t_submit <= record.t_admit <= record.t_finish <= record.t_end


async def test_t_submit_is_when_the_runtime_got_the_call():
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    records = []
    runtime.add_listener(records.append)
    try:
        before = time.time()
        job = runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
        after = time.time()
        time.sleep(0.05)  # the event loop is busy: the task's coroutine starts later
        await job
    finally:
        await _asyncflow.close_engine(flow)
    (record,) = records
    assert before <= record.t_submit <= after
    assert record.t_commit == record.t_submit and record.t_admit >= after + 0.05


async def test_the_commitment_is_the_model_reply_that_made_the_call():
    """With the fleet's model in an agent the host runs, a task's commitment is
    the end of the model call whose reply asked for it; otherwise it is when
    the runtime got the call."""
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    records, calls = [], []
    runtime.add_listener(records.append)
    fleet = Fleet([Scripted(script=calls_then_answer(("simulate", {"x": 1.5})))])
    fleet.add_listener(lambda record, reply: calls.append((record, reply)))
    graph = react_graph(
        fleet.chat_model(),
        bind_tools(runtime, [task_tool(simulate, suspend=True)]),
        KeyValueSaver(MemoryStore()),
    )
    try:
        report = await AgentHost(GraphRunner(graph), stagger=FAST).run(inputs(5))
        outside = runtime.submit(task_tool(simulate), "planner-0", "later", {"x": 1.0})
        await outside
    finally:
        await _asyncflow.close_engine(flow)
    assert report.finished == 5 and len(records) == 6
    replies = {record.agent_id: record.t_end for record, reply in calls if reply.tool_calls}
    assert len(replies) == 5
    *planned, later = records
    for record in planned:
        assert record.t_commit == replies[record.info.agent_id]
        assert record.t_commit <= record.t_submit <= record.t_admit <= record.t_end
    assert later.t_commit == later.t_submit  # outside a host: no reply known


async def test_a_call_no_fleet_reply_made_commits_when_the_runtime_gets_it():
    """The fleet notes each reply with its tool calls' ids. A later call that no
    reply made, as a node that calls a tool itself makes, is not dated by an
    earlier reply."""
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    records, calls = [], []
    runtime.add_listener(records.append)
    fleet = Fleet([Scripted(script=calls_then_answer(("simulate", {"x": 1.5})))])
    fleet.add_listener(lambda record, reply: calls.append(record))
    model = fleet.chat_model()

    class Runner:
        async def start(self, agent):
            reply = await model.ainvoke([HumanMessage("go")])
            (call,) = reply.tool_calls
            await runtime.submit(task_tool(simulate), agent.id, call["id"], call["args"])
            await runtime.submit(task_tool(simulate), agent.id, "by-hand", {"x": 2.0})
            return Finished(None)

        async def resume(self, agent, done):
            raise AssertionError("never suspends")

    try:
        report = await AgentHost(Runner(), stagger=FAST).run(inputs(1))
    finally:
        await _asyncflow.close_engine(flow)
    assert report.finished == 1
    (reply,) = calls
    made, by_hand = records
    assert made.t_commit == reply.t_end
    assert by_hand.t_commit == by_hand.t_submit > reply.t_end


async def test_an_agents_reply_times_go_when_the_agent_ends():
    """The runtime keeps each ended task, with the context it ran in, for
    repeat safety; that context holds its agent's record. The times of the
    agent's replies are needed only while it runs."""
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    records, agents = [], []
    runtime.add_listener(records.append)
    noted = time.time()

    class Runner:
        async def start(self, agent):
            _agents.note_reply(agent.id, noted, [f"call-{i}" for i in range(100)])
            agents.append(_agents._current.get())  # what the kept task's context holds
            await runtime.submit(task_tool(simulate), agent.id, "call-0", {"x": 1.0})
            return Finished(None)

        async def resume(self, agent, done):
            raise AssertionError("never suspends")

    try:
        report = await AgentHost(Runner(), stagger=FAST).run(inputs(3))
    finally:
        await _asyncflow.close_engine(flow)
    assert report.finished == 3 and all(r.t_commit == noted for r in records)
    assert runtime.stats()["retained"] == 3 and len(agents) == 3
    assert all(agent.replies == {} for agent in agents)


async def test_a_model_outside_the_fleet_commits_when_the_runtime_gets_the_call():
    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow)
    records = []
    runtime.add_listener(records.append)
    graph = react_graph(
        Scripted(script=calls_then_answer(("simulate", {"x": 1.5}))),
        bind_tools(runtime, [task_tool(simulate)]),
        KeyValueSaver(MemoryStore()),
    )
    try:
        await AgentHost(GraphRunner(graph), stagger=FAST).run(inputs(2))
    finally:
        await _asyncflow.close_engine(flow)
    assert len(records) == 2 and all(r.t_commit == r.t_submit for r in records)


async def test_a_refused_task_has_no_uid():
    class Refuse:
        async def admit(self, info):
            raise RuntimeError("budget spent")

        def done(self, info):
            pass

    flow = await open_engine("compute")
    runtime = ToolRuntime(engine=flow, order=Refuse())
    records = []
    runtime.add_listener(records.append)
    try:
        job = runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
        await asyncio.wait([job])
    finally:
        await _asyncflow.close_engine(flow)
    (record,) = records
    assert record.outcome == "refused" and record.uid is None and record.t_admit is None
    assert record.backend == "compute" and record.t_commit == record.t_submit


async def test_on_a_session_a_task_never_submitted_has_no_uid():
    session = await thread_session()
    runtime = ToolRuntime(session)
    records = []
    runtime.add_listener(records.append)
    await runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
    await session.close()
    late = runtime.submit(task_tool(simulate), "a", "c2", {"x": 1.0})
    await asyncio.wait([late])
    assert isinstance(late.exception(), TaskCancelled)
    ran, never = records
    assert ran.uid == runtime.uid("a/c1")
    assert never.outcome == "cancelled" and never.t_admit is not None and never.uid is None


async def test_on_a_session_the_record_names_the_rhapsody_task():
    session = await thread_session()
    runtime = ToolRuntime(session)
    named = ToolRuntime(session, backend="concurrent")
    records = []
    runtime.add_listener(records.append)
    named.add_listener(records.append)
    try:
        await runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
        await named.submit(task_tool(simulate), "a", "c2", {"x": 1.0})
    finally:
        await session.close()
    default, chosen = records
    # the session's default, which it routed the task to
    assert default.uid == runtime.uid("a/c1") and default.backend == "concurrent"
    assert chosen.uid == named.uid("a/c2") and chosen.backend == "concurrent"
    assert default.t_start is None and default.t_finish is None
    assert default.t_commit == default.t_submit
    assert RUNS == {"simulate": 2}


async def test_on_asyncflows_own_backend_a_task_has_a_finish_but_no_start():
    """AsyncFlow's ``LocalExecutionBackend`` never reports a task running, so
    its tasks have no ``TaskStarted`` event, and no start; the finish needs no
    event."""
    pytest.importorskip("rhapsody.telemetry.manager", reason="AsyncFlow telemetry needs rhapsody-py[telemetry]")
    from radical.asyncflow import LocalExecutionBackend

    flow = await _asyncflow.open_engine(await LocalExecutionBackend(ThreadPoolExecutor(max_workers=2), name="local"))
    telemetry = await flow.start_telemetry()
    runtime = ToolRuntime(engine=flow, telemetry=telemetry)
    records = []
    runtime.add_listener(records.append)
    try:
        value = await runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
    finally:
        await telemetry.stop()
        await _asyncflow.close_engine(flow)
    assert value == {"y": 2.0}
    (record,) = records
    assert record.backend == "local" and record.uid.startswith("task.")
    assert record.t_start is None and record.t_admit <= record.t_finish <= record.t_end
