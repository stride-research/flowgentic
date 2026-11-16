"""LangGraph agents in a host, with task, MCP and inline tools: suspension,
repeat safety, failures, order policies, telemetry, and the checkpoints written."""

import asyncio
import gc
import traceback
import weakref

import pytest
from _support import RUNS, Scripted, broken, calls_then_answer, react_graph, simulate, thread_session
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from flowgentic import _agents
from flowgentic.host import Agent, AgentHost, AgentState, Finished, Stagger, Suspended
from flowgentic.langgraph import GraphRunner, KeyValueSaver, bind_tools
from flowgentic.store import MemoryStore
from flowgentic.tools import TaskInfo, ToolRuntime, mcp_tool, task_tool

FAST = Stagger(batch=50, interval_s=0.0)


@tool
def inline_note(text: str) -> str:
    """Take a note."""
    RUNS["inline_note"] = RUNS.get("inline_note", 0) + 1
    return f"noted {text}"


@pytest.fixture(autouse=True)
def clear_runs():
    RUNS.clear()


def inputs(n: int, prefix: str = "agent"):
    return [Agent(f"{prefix}-{i}", {"messages": [HumanMessage("go")]}) for i in range(n)]


def sim_then_answer(sim: str = "simulate", note: bool = True):
    """First turn: a simulation, and a note if ``note``, at once. Then answer."""
    calls = [(sim, {"x": 1.5})] + ([("inline_note", {"text": "hi"})] if note else [])
    return calls_then_answer(*calls)


async def test_a_suspended_agent_resumes_and_nothing_runs_twice():
    session = await thread_session()
    runtime = ToolRuntime(session)
    tools = bind_tools(runtime, [task_tool(simulate, suspend=True), inline_note])
    store = MemoryStore()
    saver = KeyValueSaver(store)
    graph = react_graph(Scripted(script=sim_then_answer()), tools, saver)
    outputs = {}
    host = AgentHost(
        GraphRunner(graph),
        stagger=FAST,
        on_done=lambda r, out: outputs.__setitem__(r.id, out),
    )
    report = await host.run(inputs(20))
    await session.close()

    assert report.finished == 20
    assert all(a.suspensions == 1 for a in report.agents)
    assert RUNS == {"simulate": 20, "inline_note": 20}  # each ran once per agent
    assert runtime.stats()["repeats"] == 20  # the resume found the task
    assert saver.checkpoints_put == 40  # one at the suspension, one at the end
    assert sum(1 for k in store.keys() if k.endswith("/head")) == 20  # keep_last=1
    assert not any("/c/" in k for k in store.keys())
    final = outputs["agent-0"]["messages"][-1].content
    assert '{"y": 3.0}' in final and "noted hi" in final
    assert host.states.agent_seconds()[AgentState.TOOL_WAIT] > 0
    # the host holds no task of an agent that has ended
    assert all(_agents.pending(f"agent-{i}/call-0") is None for i in range(20))


def left_by(run_objects: tuple) -> list:
    return [type(o).__name__ for o in gc.get_objects() if isinstance(o, run_objects)]


async def test_a_task_tool_suspension_leaves_nothing_for_the_collector():
    """The task tool records the interrupt it raises, so the runner frees the
    suspended run when it stops, not at the collector's next pass."""
    from langgraph.errors import GraphInterrupt
    from langgraph.pregel._runner import PregelRunner

    session = await thread_session()
    runtime = ToolRuntime(session)
    graph = react_graph(
        Scripted(script=sim_then_answer(note=False)),
        bind_tools(runtime, [task_tool(simulate, suspend=True)]),
        KeyValueSaver(MemoryStore()),
    )
    runner = GraphRunner(graph)
    agent = Agent("agent-0", {"messages": [HumanMessage("go")]})
    gc.collect()
    gc.disable()
    try:
        step = await runner.start(agent)
        assert isinstance(step, Suspended) and len(step.waits) == 1
        left = left_by((GraphInterrupt, PregelRunner))
        await asyncio.wait([runtime._jobs[key] for _, key in step.waits])
        done = {rid: {"flowgentic_done": key} for rid, key in step.waits}
        step = await runner.resume(agent, done)
        assert isinstance(step, Finished)
        left_at_end = left_by((GraphInterrupt, PregelRunner))
    finally:
        gc.enable()
        await session.close()
    assert left == [] and left_at_end == []


async def test_a_kept_task_keeps_nothing_of_the_run_that_asked_for_it():
    """The runtime keeps ended tasks, for repeat safety. An asyncio task keeps
    the context it was created in, so a task created in its caller's context
    would keep the framework's run alive with it: for LangGraph, the agent's
    whole state, for the rest of the campaign."""
    import contextvars

    class State:
        pass

    run_var: contextvars.ContextVar = contextvars.ContextVar("run")
    session = await thread_session()
    runtime = ToolRuntime(session)
    state = State()
    alive = weakref.ref(state)

    def ask():
        run_var.set(state)
        return runtime.submit(task_tool(simulate), "agent-1", "c1", {"x": 1.0})

    job = contextvars.copy_context().run(ask)
    assert await job == {"y": 2.0}
    await session.close()
    del state
    assert alive() is None
    assert runtime.stats()["retained"] == 1


@pytest.mark.parametrize("suspend", [False, True], ids=["inline", "suspended"])
@pytest.mark.parametrize("fn", [simulate, broken], ids=["ok", "failed"])
async def test_after_a_run_nothing_keeps_the_agents_messages(fn, suspend):
    """The runtime keeps each ended task, and a failed task keeps its exception.
    Raised in the tool, that exception would keep the tool's frames, and on
    CPython 3.12 those of the LangGraph run around it, with the agent's messages."""
    session = await thread_session()
    runtime = ToolRuntime(session)
    graph = react_graph(
        Scripted(script=sim_then_answer(sim=fn.__name__, note=False)),
        bind_tools(runtime, [task_tool(fn, suspend=suspend)]),
        KeyValueSaver(MemoryStore()),
    )
    runner = GraphRunner(graph)
    agent = Agent("agent-0", {"messages": []})
    gc.collect()
    gc.disable()
    try:
        step = await runner.start(agent)
        if suspend:
            await asyncio.wait([runtime._jobs[key] for _, key in step.waits])
            step = await runner.resume(agent, {rid: {"flowgentic_done": key} for rid, key in step.waits})
        await session.close()
        messages = step.output["messages"]
        assert [m.type for m in messages] == ["ai", "tool", "ai"]
        kept = [weakref.ref(m) for m in messages]
        del step, messages
        assert [m() for m in kept] == [None, None, None]
    finally:
        gc.enable()
    job = runtime._jobs["agent-0/call-0"]
    error = job.exception()
    assert (error is None) == (fn is simulate)
    if error is not None:  # no frame of the tool that waited on the task
        frames = [f.f_code.co_filename for f, _ in traceback.walk_tb(error.__traceback__)]
        assert not [f for f in frames if f.endswith(("langgraph/tools.py", "langchain_core/tools/base.py"))]


async def test_without_suspension_the_agent_waits_in_memory():
    session = await thread_session()
    runtime = ToolRuntime(session)
    saver = KeyValueSaver(MemoryStore())
    graph = react_graph(
        Scripted(script=sim_then_answer(note=False)),
        bind_tools(runtime, [task_tool(simulate)]),
        saver,
    )
    report = await AgentHost(GraphRunner(graph), stagger=FAST).run(inputs(5))
    await session.close()
    assert report.finished == 5 and all(a.suspensions == 0 for a in report.agents)
    assert saver.checkpoints_put == 5  # only at the end, with exit durability


async def test_suspend_can_be_decided_per_call():
    session = await thread_session()
    runtime = ToolRuntime(session)
    sim = task_tool(simulate, suspend=lambda agent, args: agent.endswith(("0", "2")))
    graph = react_graph(
        Scripted(script=sim_then_answer(note=False)),
        bind_tools(runtime, [sim]),
        KeyValueSaver(MemoryStore()),
    )
    report = await AgentHost(GraphRunner(graph), stagger=FAST).run(inputs(4))
    await session.close()
    assert [a.suspensions for a in report.agents] == [1, 0, 1, 0]


async def test_a_failed_task_reaches_the_model_as_a_tool_error():
    session = await thread_session()
    runtime = ToolRuntime(session)
    graph = react_graph(
        Scripted(script=sim_then_answer(sim="broken", note=False)),
        bind_tools(runtime, [task_tool(broken, suspend=True)]),
        KeyValueSaver(MemoryStore()),
    )
    outputs = {}
    report = await AgentHost(
        GraphRunner(graph), stagger=FAST, on_done=lambda r, o: outputs.__setitem__(r.id, o)
    ).run(inputs(1))
    await session.close()
    assert report.finished == 1
    message = outputs["agent-0"]["messages"][-2]
    assert message.type == "tool" and message.status == "error"
    assert "broken failed: ValueError: no convergence at 1.5" in message.content
    assert RUNS == {"broken": 1} and runtime.stats()["failed"] == 1


async def test_repeat_safety_and_retention():
    session = await thread_session()
    runtime = ToolRuntime(session, retained=2)
    sim = task_tool(simulate)
    first = runtime.submit(sim, "a", "c1", {"x": 1.0})
    again = runtime.submit(sim, "a", "c1", {"x": 1.0})
    assert first is again
    assert await first == {"y": 2.0}
    for i in range(3):
        await runtime.submit(sim, "a", f"c{i + 2}", {"x": 1.0})
    # c1 was dropped: retained keeps the last 2 ended tasks, so it runs again
    await runtime.submit(sim, "a", "c1", {"x": 1.0})
    await session.close()
    assert RUNS["simulate"] == 5
    assert runtime.uid("a b/c:1") == "flowgentic.a_b.c_1"
    with pytest.raises(ValueError, match="at least 1"):
        ToolRuntime(session, retained=0)


async def test_a_resumed_agent_finds_its_task_after_the_runtime_dropped_it():
    """``retained`` bounds the ended tasks the runtime keeps, but an agent finds
    the tasks it asked for until it ends: with room for one ended task, agents
    whose tasks end before they resume still run each simulation once."""
    session = await thread_session()
    runtime = ToolRuntime(session, retained=1)
    graph = react_graph(
        Scripted(script=sim_then_answer(note=False)),
        bind_tools(runtime, [task_tool(simulate, suspend=True)]),
        KeyValueSaver(MemoryStore()),
    )
    report = await AgentHost(GraphRunner(graph), stagger=FAST).run(inputs(20))
    await session.close()
    assert report.finished == 20 and all(a.suspensions == 1 for a in report.agents)
    assert RUNS == {"simulate": 20}
    assert runtime.stats()["repeats"] == 20 and runtime.stats()["retained"] == 1


async def test_only_the_asking_agents_own_registrations_are_found():
    """A future registered under the same key outside an agent, as a tool run
    without a host leaves it, is not taken for the runtime's task."""
    session = await thread_session()
    runtime = ToolRuntime(session)
    stale = asyncio.get_running_loop().create_future()
    _agents.register_pending("a/c1", stale)
    try:
        job = runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
        assert job is not stale and await job == {"y": 2.0}
    finally:
        _agents.forget_pending("a/c1")
        await session.close()
    assert RUNS == {"simulate": 1}


async def test_a_task_outside_a_host_is_not_registered_for_one():
    session = await thread_session()
    runtime = ToolRuntime(session)
    job = runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
    assert await job == {"y": 2.0}
    await session.close()
    assert _agents.pending("a/c1") is None


class Held:
    """An order policy that holds every task until its gate opens."""

    def __init__(self):
        self.gate = asyncio.Event()
        self.admitted: list[str] = []
        self.ended: list[str] = []

    async def admit(self, info: TaskInfo) -> None:
        await self.gate.wait()
        self.admitted.append(info.key)

    def done(self, info: TaskInfo) -> None:
        self.ended.append(info.key)


async def test_an_order_policy_decides_when_tasks_are_submitted():
    session = await thread_session()
    order = Held()
    runtime = ToolRuntime(session, order=order)
    records = []
    runtime.add_listener(records.append)
    job = runtime.submit(task_tool(simulate), "a", "c1", {"x": 2.0})
    held = runtime.submit(task_tool(simulate), "a", "c2", {"x": 3.0})
    await asyncio.sleep(0.05)
    assert not job.done() and RUNS == {}
    held.cancel()  # cancelled while it waits for admission
    with pytest.raises(asyncio.CancelledError):
        await held
    order.gate.set()
    assert await job == {"y": 4.0}
    await session.close()
    assert order.admitted == order.ended == ["a/c1"]  # done only for admitted tasks
    assert [(r.info.key, r.outcome) for r in records] == [("a/c2", "cancelled"), ("a/c1", "ok")]


async def test_cancelling_a_tool_that_waits_cancels_its_task():
    """The tool waits for its task without raising the task's exception, and
    cancelling the tool still cancels the task, as awaiting the task would."""
    session = await thread_session()
    runtime = ToolRuntime(session, order=Held())
    (tool_,) = bind_tools(runtime, [task_tool(simulate)])
    call = {"name": "simulate", "args": {"x": 1.0}, "id": "c1", "type": "tool_call"}
    waiting = asyncio.create_task(tool_.ainvoke(call, {"configurable": {"thread_id": "a"}}))
    await asyncio.sleep(0.05)
    job = runtime._jobs["a/c1"]
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await asyncio.wait([job])
    await session.close()
    assert job.cancelled() and RUNS == {}


def write_result(x: float, key: str, scale: int) -> dict:
    """What the worker runs: it needs a key and a scale the model never sees."""
    RUNS["write_result"] = RUNS.get("write_result", 0) + 1
    return {"key": key, "y": scale * x, "t_start": 1.0, "t_end": 2.0}


class SimArgs(BaseModel):
    x: float


async def test_the_model_and_the_worker_see_different_things_and_budgets_refuse():
    class Budget:
        def __init__(self, most):
            self.most, self.used, self.ended = most, {}, []

        async def admit(self, info):
            n = self.used.get(info.agent_id, 0)
            if n >= self.most:
                raise RuntimeError(f"budget spent: at most {self.most} per agent")
            self.used[info.agent_id] = n + 1

        def done(self, info):
            self.ended.append(info.key)

    session = await thread_session()
    budget = Budget(most=1)
    runtime = ToolRuntime(session, order=budget)
    records = []
    runtime.add_listener(records.append)
    sim = task_tool(
        write_result,
        name="simulate",
        description="Simulate x.",
        args_schema=SimArgs,
        prepare=lambda agent, call, args: {**args, "key": f"res/{agent}/{call}", "scale": 10},
        output=lambda value: {"result_key": value["key"]},
    )
    (tool_,) = bind_tools(runtime, [sim])
    assert set(tool_.tool_call_schema.model_json_schema()["properties"]) == {"x"}
    config = {"configurable": {"thread_id": "agent-1"}}
    call = {"name": "simulate", "args": {"x": 2.0}, "id": "c1", "type": "tool_call"}
    out = await tool_.ainvoke(call, config)
    assert out.content == '{"result_key": "res/agent-1/c1"}'
    refused = await tool_.ainvoke({**call, "args": {"x": 3.0}, "id": "c2"}, config)
    assert "budget spent: at most 1 per agent" in refused.content
    await session.close()
    assert RUNS == {"write_result": 1}
    ok, no = records
    assert ok.ok and ok.value["y"] == 20.0 and ok.info.kwargs["key"] == "res/agent-1/c1"
    assert ok.t_submit <= ok.t_admit <= ok.t_end
    assert no.outcome == "refused" and no.t_admit is None and "budget spent" in no.error
    assert budget.ended == ["agent-1/c1"]  # a refused task was never admitted
    assert runtime.stats() == {
        "submitted": 2,
        "repeats": 0,
        "failed": 0,
        "refused": 1,
        "retained": 2,
    }


async def test_a_failing_listener_leaves_the_task_alone(caplog):
    session = await thread_session()
    runtime = ToolRuntime(session)
    runtime.add_listener(lambda record: 1 / 0)
    seen = []
    runtime.add_listener(seen.append)
    assert await runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0}) == {"y": 2.0}
    await session.close()
    assert [r.outcome for r in seen] == ["ok"]
    assert "a task listener failed on a/c1" in caplog.text


class LookupArgs(BaseModel):
    material: str


class FakeService:
    def __init__(self):
        self.calls = []

    async def call(self, uid, tool, args, *, idempotent=False):
        self.calls.append((uid, tool, args, idempotent))
        if args["material"] == "bad":
            raise RuntimeError("server gone")
        return {"material": args["material"], "score": 0.5}


async def test_mcp_tools_go_through_the_service():
    service = FakeService()
    runtime = ToolRuntime(service=service)
    lookup = mcp_tool(
        "lookup", description="Look a material up.", args_schema=LookupArgs, idempotent=True
    )
    write = mcp_tool("write", description="Write a material.", args_schema=LookupArgs)
    lookup_, write_ = bind_tools(runtime, [lookup, write])
    assert "tool_call_id" not in lookup_.tool_call_schema.model_json_schema()["properties"]
    config = {"configurable": {"thread_id": "agent-7"}}
    call = {"name": "lookup", "args": {"material": "x"}, "id": "c9", "type": "tool_call"}
    out = await lookup_.ainvoke(call, config)
    assert '"score": 0.5' in out.content
    bad = await lookup_.ainvoke({**call, "args": {"material": "bad"}}, config)
    assert "lookup failed: RuntimeError: server gone" in bad.content
    await write_.ainvoke({**call, "name": "write", "id": "c10"}, config)
    assert service.calls == [
        ("agent-7/c9", "lookup", {"material": "x"}, True),
        ("agent-7/c9", "lookup", {"material": "bad"}, True),
        ("agent-7/c10", "write", {"material": "x"}, False),  # not idempotent by default
    ]


def points(reader, name: str) -> list:
    """The data points of metric ``name`` in the reader's latest collection."""
    data = reader.get_metrics_data()
    return [
        point
        for resource in (data.resource_metrics if data else [])
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


async def test_task_and_service_calls_are_spans_and_metrics(span_exporter, metric_reader):
    session = await thread_session()
    runtime = ToolRuntime(session, FakeService())
    graph = react_graph(
        Scripted(
            script=calls_then_answer(
                ("broken", {"x": 1.0}), ("lookup", {"material": "y"}), ("inline_note", {"text": "t"})
            )
        ),
        bind_tools(
            runtime,
            [
                task_tool(broken, suspend=True),
                mcp_tool("lookup", description="Look up.", args_schema=LookupArgs),
                inline_note,
            ],
        ),
        KeyValueSaver(MemoryStore()),
    )
    report = await AgentHost(GraphRunner(graph), stagger=FAST).run(inputs(1, "tel"))
    await session.close()
    assert report.finished == 1

    spans = {s.name: s for s in span_exporter.get_finished_spans()}
    task_span, service_span = spans["execute_tool broken"], spans["execute_tool lookup"]
    assert task_span.attributes["gen_ai.operation.name"] == "execute_tool"
    assert task_span.attributes["gen_ai.tool.call.id"] == "call-0"
    assert task_span.attributes["flowgentic.tool.kind"] == "task"
    assert task_span.attributes["flowgentic.tool.outcome"] == "error"
    assert task_span.attributes["error.type"] == "ValueError"
    assert service_span.attributes["flowgentic.tool.kind"] == "service"
    assert service_span.attributes["flowgentic.tool.outcome"] == "ok"
    agent_span = spans["invoke_agent tel-0"]
    assert task_span.parent.span_id == agent_span.context.span_id  # inside the agent's trace
    assert task_span.context.trace_id == agent_span.context.trace_id

    durations = {
        (p.attributes["gen_ai.tool.name"], p.attributes["flowgentic.tool.outcome"]): p
        for p in points(metric_reader, "flowgentic.tool.duration")
    }
    assert durations[("broken", "error")].attributes["flowgentic.tool.kind"] == "task"
    assert durations[("lookup", "ok")].count >= 1


async def test_many_agents_in_one_host():
    session = await thread_session(workers=16)
    runtime = ToolRuntime(session)
    graph = react_graph(
        Scripted(script=sim_then_answer()),
        bind_tools(runtime, [task_tool(simulate, suspend=True), inline_note]),
        KeyValueSaver(MemoryStore()),
    )
    report = await AgentHost(GraphRunner(graph), max_live_agents=200, stagger=FAST).run(
        inputs(1000)
    )
    await session.close()
    assert report.finished == 1000
    assert RUNS == {"simulate": 1000, "inline_note": 1000}


def test_a_tool_that_is_not_a_tool_is_refused():
    with pytest.raises(TypeError, match="not a tool"):
        bind_tools(ToolRuntime(), [object()])
