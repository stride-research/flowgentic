"""LangGraph agents in a host, with task, MCP and inline tools: suspension,
repeat safety, failures, order policies, telemetry, and the checkpoints written."""

import asyncio
import gc
import threading
import time
import traceback
import weakref
from types import SimpleNamespace

import pytest
from _support import RUNS, Scripted, broken, calls_then_answer, react_graph, simulate, thread_session
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel

from flowgentic import _agents
from flowgentic.host import Agent, AgentHost, AgentState, Finished, Stagger, Suspended
from flowgentic.langgraph import GraphRunner, KeyValueSaver, bind_tools
from flowgentic.store import MemoryStore
from flowgentic.tools import TaskCancelled, TaskInfo, ToolRuntime, mcp_tool, task_tool
from flowgentic.tools.service import ToolServiceClient, ToolServiceError

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
    # the host holds no task of an agent that has ended, and the runtime keeps
    # each one's outcome, not the task
    assert runtime._jobs == {} and runtime.stats()["retained"] == 20
    assert all(_agents.pending(key) is None for key in runtime._ended)


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
        await until(lambda: all(key in runtime._ended for _, key in step.waits))
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
            await until(lambda: all(key in runtime._ended for _, key in step.waits))
            step = await runner.resume(agent, {rid: {"flowgentic_done": key} for rid, key in step.waits})
        await session.close()
        messages = step.output["messages"]
        assert [m.type for m in messages] == ["ai", "tool", "ai"]
        kept = [weakref.ref(m) for m in messages]
        del step, messages
        assert [m() for m in kept] == [None, None, None]
    finally:
        gc.enable()
    (kept,) = runtime._ended.values()  # the task's outcome: its value or its exception
    error = getattr(kept, "error", None)
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
    assert runtime.uid("a b/c:1") == "flowgentic.a_x20_b.c_x3a_1"
    with pytest.raises(ValueError, match="at least 1"):
        ToolRuntime(session, retained=0)


@pytest.mark.parametrize("fn", [simulate, broken], ids=["ok", "failed"])
async def test_a_task_description_keeps_no_payload_once_read(fn):
    """RHAPSODY's session keeps every task's description for its life, with the
    task's arguments, its result and the backend's copies of it. Once Flowgentic
    has read the outcome, the description keeps none of them."""
    session = await thread_session()
    runtime = ToolRuntime(session)
    job = runtime.submit(task_tool(fn), "a", "c1", {"x": 1.5})
    await asyncio.wait([job])
    description = session._tasks[runtime.uid("a/c1")]
    await session.close()
    if fn is simulate:
        assert job.result() == {"y": 3.0}
    else:
        assert "no convergence at 1.5" in str(job.exception())
    assert description["return_value"] is None and description["exception"] is None
    assert not description["args"] and not description["stdout"] and not description["stderr"]


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
    _agents.register_pending("a/c1", stale)  # held until a host run ends
    try:
        job = runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
        assert job is not stale and await job == {"y": 2.0}
    finally:
        _agents._unowned.pop("a/c1", None)  # no host run here to release it
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


async def test_cancelling_a_tool_that_waits_leaves_its_started_task_running():
    """A started task is shared by every call with its key, and RHAPSODY does
    not stop it: cancelling the wait, as cancelling its agent does, leaves it
    running, and the call made again gets the task's result."""
    session = await thread_session()
    order = Held()
    order.gate.set()
    runtime = ToolRuntime(session, order=order)
    (tool_,) = bind_tools(runtime, [task_tool(slow_simulate)])
    call = {"name": "slow_simulate", "args": {"x": 1.0}, "id": "c1", "type": "tool_call"}
    config = {"configurable": {"thread_id": "a"}}
    waiting = asyncio.create_task(tool_.ainvoke(call, config))
    await asyncio.sleep(0.05)
    job = runtime._jobs["a/c1"]
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert not job.done()
    again = await tool_.ainvoke(call, config)
    await session.close()
    assert again.content == '{"y": 2.0}' and again.status == "success"
    assert not job.cancelled() and RUNS == {"slow_simulate": 1}
    assert order.admitted == order.ended == ["a/c1"]


async def test_a_queued_task_no_call_waits_for_is_withdrawn():
    """Tasks that their order has not admitted yet, whose agents are all gone,
    never reach RHAPSODY; the started one runs on. A call made again for a
    withdrawn task submits it afresh."""
    session = await thread_session()
    order = OneAtATime()
    runtime = ToolRuntime(session, order=order)
    records = []
    runtime.add_listener(records.append)
    (tool_,) = bind_tools(runtime, [task_tool(slow_simulate)])

    def call(i):
        return (
            {"name": "slow_simulate", "args": {"x": float(i)}, "id": "c1", "type": "tool_call"},
            {"configurable": {"thread_id": f"agent-{i}"}},
        )

    waits = [asyncio.create_task(tool_.ainvoke(*call(i))) for i in range(3)]
    await until(lambda: len(runtime._jobs) == 3 and order.slot.locked())  # one runs
    for wait in waits:
        wait.cancel()
    await asyncio.gather(*waits, return_exceptions=True)
    await asyncio.sleep(0.6)  # time for two more tasks, had they been submitted
    assert RUNS == {"slow_simulate": 1}
    (ran,) = [r for r in records if r.outcome == "ok"]
    withdrawn = [r for r in records if r is not ran]
    assert [(r.outcome, r.t_admit) for r in withdrawn] == [("cancelled", None)] * 2
    assert runtime._jobs == {} and list(runtime._ended) == [ran.info.key]
    again = await tool_.ainvoke(*call(int(withdrawn[0].info.args["x"])))
    await session.close()
    assert again.status == "success" and RUNS == {"slow_simulate": 2}


async def test_a_queued_task_stays_while_a_call_still_waits_for_it():
    """Calls made again with one key share its task: cancelling one of their
    waits leaves the task to the others."""
    session = await thread_session()
    order = Held()
    runtime = ToolRuntime(session, order=order)
    (tool_,) = bind_tools(runtime, [task_tool(simulate)])
    call = {"name": "simulate", "args": {"x": 1.0}, "id": "c1", "type": "tool_call"}
    config = {"configurable": {"thread_id": "a"}}
    first = asyncio.create_task(tool_.ainvoke(call, config))
    second = asyncio.create_task(tool_.ainvoke(call, config))
    await asyncio.sleep(0.05)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    order.gate.set()
    out = await second
    await session.close()
    assert out.content == '{"y": 2.0}' and RUNS == {"simulate": 1}
    assert runtime.stats()["submitted"] == 1 and runtime.stats()["repeats"] == 1


async def test_a_stopped_host_withdraws_the_queued_tasks_of_its_suspended_agents():
    session = await thread_session()
    order = OneAtATime()
    runtime = ToolRuntime(session, order=order)
    graph = react_graph(
        Scripted(script=calls_then_answer(("slow_simulate", {"x": 1.0}))),
        bind_tools(runtime, [task_tool(slow_simulate, suspend=True)]),
        KeyValueSaver(MemoryStore()),
    )
    host = AgentHost(GraphRunner(graph), stagger=FAST)
    run = asyncio.create_task(host.run(inputs(3)))
    # Every agent has suspended, and one task runs.
    await until(
        lambda: order.slot.locked()
        and host.states.counts().get(AgentState.TOOL_WAIT, 0) == 3
    )
    run.cancel()
    await asyncio.gather(run, return_exceptions=True)
    await asyncio.sleep(0.6)
    await session.close()
    assert RUNS == {"slow_simulate": 1}


async def test_closing_the_session_ends_its_tasks_and_frees_their_places():
    """A campaign stops its host, then closes its RHAPSODY session, which drops
    the tasks it still runs. A task left running by its cancelled wait ends,
    cancelled, and gives its place in its order back; a task its order admits
    after the session closed fails at once."""
    session = await thread_session()
    order = OneAtATime()
    runtime = ToolRuntime(session, order=order)
    records = []
    runtime.add_listener(records.append)
    (tool_,) = bind_tools(runtime, [task_tool(slow_simulate)])

    def call(agent):
        return (
            {"name": "slow_simulate", "args": {"x": 1.0}, "id": "c1", "type": "tool_call"},
            {"configurable": {"thread_id": agent}},
        )

    running = asyncio.create_task(tool_.ainvoke(*call("a")))
    await until(lambda: order.slot.locked())  # a's task runs
    queued = asyncio.create_task(tool_.ainvoke(*call("b")))  # b's waits for its turn
    await until(lambda: len(runtime._jobs) == 2)
    job = runtime._jobs["a/c1"]
    running.cancel()
    await asyncio.gather(running, return_exceptions=True)
    await session.close()
    out = await asyncio.wait_for(queued, 2)
    assert job.done() and isinstance(job.exception(), TaskCancelled)
    assert out.status == "error" and "TaskCancelled: the RHAPSODY session is closed" in out.content
    assert [(r.info.agent_id, r.outcome) for r in records] == [("a", "cancelled"), ("b", "cancelled")]
    assert "the RHAPSODY session closed before the task ended" in records[0].error
    # The order is free for the next session's tasks.
    later = await thread_session()
    again = ToolRuntime(later, order=order).submit(task_tool(simulate), "c", "c1", {"x": 2.0})
    assert await asyncio.wait_for(again, 2) == {"y": 4.0}
    await later.close()


async def test_the_session_keeps_nothing_of_a_failed_task_once_read(caplog):
    """RHAPSODY's session keeps each task's description for its life. Once a
    failed task's outcome is read, the description keeps neither its exception
    nor, through the exception's traceback, the runtime and its tasks. The
    runtime reports each outcome itself, so a failure that no call read, as
    when its agent was stopped, is no asyncio error either."""
    session = await thread_session()
    runtime = ToolRuntime(session)
    job = runtime.submit(task_tool(broken), "a", "c1", {"x": 1.0})
    await asyncio.wait([job])
    kept = weakref.ref(runtime), weakref.ref(job)
    del job, runtime
    gc.collect()
    assert [ref() for ref in kept] == [None, None]
    assert "never retrieved" not in caplog.text
    await session.close()


async def test_a_task_rhapsody_cancels_ends_cancelled():
    """A task RHAPSODY cancels ends without a value: it is no success."""
    session = await thread_session()
    order = OneAtATime()
    runtime = ToolRuntime(session, order=order)
    job = runtime.submit(task_tool(slow_simulate), "a", "c1", {"x": 1.0})
    await asyncio.sleep(0.05)
    (backend,) = session.backends.values()
    assert await backend.cancel_task(runtime.uid("a/c1"))
    await asyncio.wait_for(asyncio.wait([job]), 2)
    assert isinstance(job.exception(), TaskCancelled)
    assert "RHAPSODY cancelled the task" in str(job.exception())
    assert not order.slot.locked()
    await session.close()


async def test_a_call_made_again_after_a_timed_out_wait_gets_the_task():
    session = await thread_session()
    runtime = ToolRuntime(session)
    (tool_,) = bind_tools(runtime, [task_tool(slow_simulate)])
    call = {"name": "slow_simulate", "args": {"x": 1.0}, "id": "c1", "type": "tool_call"}
    config = {"configurable": {"thread_id": "a"}}
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(tool_.ainvoke(call, config), 0.05)
    out = await tool_.ainvoke(call, config)
    await session.close()
    assert out.content == '{"y": 2.0}' and RUNS == {"slow_simulate": 1}
    assert not asyncio.current_task().cancelling()


class OneAtATime:
    """An order policy that admits one task at a time."""

    def __init__(self):
        self.slot = asyncio.Semaphore(1)

    async def admit(self, info: TaskInfo) -> None:
        await self.slot.acquire()

    def done(self, info: TaskInfo) -> None:
        self.slot.release()


async def test_a_cancelled_wait_keeps_its_tasks_place_in_the_order_until_it_ends():
    session = await thread_session()
    runtime = ToolRuntime(session, order=OneAtATime())
    (tool_,) = bind_tools(runtime, [task_tool(counted_simulate)])
    calls = [
        ({"name": "counted_simulate", "args": {"x": x}, "id": "c1", "type": "tool_call"},
         {"configurable": {"thread_id": agent}})
        for agent, x in (("a", 1.0), ("b", 2.0))
    ]
    waits = [asyncio.create_task(tool_.ainvoke(call, config)) for call, config in calls]
    await asyncio.sleep(0.1)  # a's task runs; b's waits for its turn
    jobs = list(runtime._jobs.values())
    waits[0].cancel()
    await asyncio.gather(*waits, return_exceptions=True)
    await asyncio.wait(jobs)
    await session.close()
    assert COUNTED["peak"] == 1  # never two at once under a one-at-a-time order
    assert [job.result() for job in jobs] == [{"y": 2.0}, {"y": 4.0}]


async def test_a_task_cancelled_by_its_owner_reaches_the_model_as_an_error():
    """A started task's future cancelled directly, not through a tool's wait,
    is a failed call for the model to read, not a CancelledError that would
    fail its agent."""
    session = await thread_session()
    runtime = ToolRuntime(session)
    (tool_,) = bind_tools(runtime, [task_tool(slow_simulate)])
    call = {"name": "slow_simulate", "args": {"x": 1.0}, "id": "c1", "type": "tool_call"}
    config = {"configurable": {"thread_id": "a"}}
    job = runtime.submit(task_tool(slow_simulate), "a", "c1", {"x": 1.0})
    await asyncio.sleep(0.05)
    job.cancel()
    out = await tool_.ainvoke(call, config)
    await session.close()
    assert out.status == "error" and "slow_simulate failed: its task was cancelled" in out.content


async def until(condition, timeout: float = 5.0) -> None:
    """Wait until ``condition()`` holds, failing after ``timeout`` seconds."""
    end = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < end, "condition not reached"
        await asyncio.sleep(0.01)


def slow_simulate(x: float) -> dict:
    """A simulation that takes a while."""
    time.sleep(0.3)
    RUNS["slow_simulate"] = RUNS.get("slow_simulate", 0) + 1
    return {"y": 2 * x}


COUNTED = {"running": 0, "peak": 0}
_COUNTED_LOCK = threading.Lock()


def counted_simulate(x: float) -> dict:
    """A simulation that counts how many run at once."""
    with _COUNTED_LOCK:
        COUNTED["running"] += 1
        COUNTED["peak"] = max(COUNTED["peak"], COUNTED["running"])
    time.sleep(0.3)
    with _COUNTED_LOCK:
        COUNTED["running"] -= 1
    return {"y": 2 * x}


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
        "lost": 0,
        "retries": 0,
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
            raise ToolServiceError("server gone")
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
    assert "lookup failed: ToolServiceError: server gone" in bad.content
    await write_.ainvoke({**call, "name": "write", "id": "c10"}, config)
    assert service.calls == [
        ("agent-7/c9", "lookup", {"material": "x"}, True),
        ("agent-7/c9", "lookup", {"material": "bad"}, True),
        ("agent-7/c10", "write", {"material": "x"}, False),  # not idempotent by default
    ]


@pytest.mark.parametrize("setup", ["task, no session", "mcp, no service", "mcp, client not started"])
async def test_a_runtime_set_up_wrong_fails_the_agent(setup):
    """A misconfigured campaign is loud: a tool whose runtime lacks the session
    or the started service client it needs fails its agent, with ToolNode's
    default error handling, rather than tell the model that the call failed."""
    import queue

    if setup.startswith("task"):
        runtime, tool_, call = ToolRuntime(), task_tool(simulate), ("simulate", {"x": 1.0})
        expected = "task tools need a ToolRuntime with a RHAPSODY session"
    else:
        client = ToolServiceClient(queue.Queue(), queue.Queue(), index=0) if "client" in setup else None
        runtime = ToolRuntime(service=client)
        tool_ = mcp_tool("lookup", description="Look up.", args_schema=LookupArgs)
        call = ("lookup", {"material": "x"})
        expected = "ToolServiceClient.start() was not called" if client else "need a ToolRuntime with a tool service"
    graph = react_graph(
        Scripted(script=calls_then_answer(call)),
        bind_tools(runtime, [tool_]),
        KeyValueSaver(MemoryStore()),
        handle_tool_errors=None,
    )
    report = await AgentHost(GraphRunner(graph), stagger=FAST).run(inputs(1))
    (agent,) = report.agents
    assert agent.status == "failed" and agent.error.startswith("ToolSetupError: ")
    assert expected in agent.error


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
    agent_span = spans["invoke_agent"]
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


async def simulate_async(x: float) -> dict:
    """Run a simulation of x, asynchronously."""
    from opentelemetry import trace

    await asyncio.sleep(0)
    TRACES.append(trace.get_current_span().get_span_context().trace_id)
    RUNS["simulate_async"] = RUNS.get("simulate_async", 0) + 1
    return {"y": 2 * x}


TRACES: list[int] = []


async def test_an_async_task_tool_returns_its_value(span_exporter):
    """RHAPSODY's backends run an async function's coroutine; Flowgentic's
    wrapper around it must do the same, rather than hand back the coroutine."""
    TRACES.clear()
    session = await thread_session()
    runtime = ToolRuntime(session)
    graph = react_graph(
        Scripted(script=sim_then_answer(sim="simulate_async", note=False)),
        bind_tools(runtime, [task_tool(simulate_async, suspend=True)]),
        KeyValueSaver(MemoryStore()),
    )
    outputs = {}
    report = await AgentHost(
        GraphRunner(graph), stagger=FAST, on_done=lambda r, o: outputs.__setitem__(r.id, o)
    ).run(inputs(1, "async"))
    await session.close()
    assert report.finished == 1 and report.agents[0].suspensions == 1
    assert outputs["async-0"]["messages"][-1].content == '{"y": 3.0}'
    assert RUNS == {"simulate_async": 1}
    # the coroutine ran in the caller's trace context
    (task_span,) = [s for s in span_exporter.get_finished_spans() if s.name == "execute_tool simulate_async"]
    assert TRACES == [task_span.context.trace_id]


async def test_mcp_arguments_reach_the_service_as_json():
    """LangChain hands a tool the arguments parsed by its schema: a nested
    model and an enum arrive as instances of the application's classes. The
    service process may not import those classes, and MCP takes JSON."""
    import enum
    import queue

    from flowgentic.tools.service import serve

    class Unit(str, enum.Enum):
        K = "K"

    class Range(BaseModel):
        lo: float
        hi: float

    class Args(BaseModel):
        material: str
        temperature: Range
        unit: Unit

    seen = []

    class Pool:
        servers = ["fake"]

        async def start(self, *args, **kwargs):
            pass

        async def call(self, tool, args, *, timeout=None, idempotent=False):
            seen.append(args)
            return SimpleNamespace(is_error=False, structured_content={"ok": True}, content=[])

        def stats(self):
            return {}

        async def close(self, timeout=10.0):
            pass

    requests, replies = queue.Queue(), queue.Queue()
    service = asyncio.create_task(serve(requests, [replies], Pool()))
    client = ToolServiceClient(requests, replies, 0)
    client.start()
    try:
        (lookup,) = bind_tools(
            ToolRuntime(service=client), [mcp_tool("lookup", description="Look up.", args_schema=Args)]
        )
        call = {
            "name": "lookup",
            "type": "tool_call",
            "id": "c1",
            "args": {"material": "Fe", "temperature": {"lo": 1, "hi": 2}, "unit": "K"},
        }
        out = await asyncio.wait_for(lookup.ainvoke(call, {"configurable": {"thread_id": "a"}}), 5)
    finally:
        requests.put(None)
        await asyncio.wait_for(service, 5)
        client.stop()
    assert out.content == '{"ok": true}'
    assert seen == [{"material": "Fe", "temperature": {"lo": 1.0, "hi": 2.0}, "unit": "K"}]
    assert type(seen[0]["temperature"]) is dict and type(seen[0]["unit"]) is str


def simulate_forever(messages):
    """A model that calls a tool on every turn, never answering."""
    turn = sum(1 for m in messages if m.type == "ai")
    return AIMessage("", tool_calls=[{"name": "simulate", "args": {"x": 1.0}, "id": f"c{turn}"}])


def simulate_times(n: int):
    """A model that calls a tool ``n`` times, one call per turn, then answers."""

    def script(messages):
        done = sum(1 for m in messages if m.type == "tool")
        if done < n:
            return AIMessage("", tool_calls=[{"name": "simulate", "args": {"x": 1.0}, "id": f"c{done}"}])
        return AIMessage("done")

    return script


@pytest.mark.parametrize("suspend", [False, True], ids=["in-memory", "suspended"])
async def test_the_recursion_limit_holds_across_suspensions(suspend):
    """Each resume is a new LangGraph run, which counts its steps afresh: on its
    own, the limit would never stop an agent that suspends on every turn."""
    session = await thread_session()
    runtime = ToolRuntime(session)
    graph = react_graph(
        Scripted(script=simulate_forever),
        bind_tools(runtime, [task_tool(simulate, suspend=suspend)]),
        KeyValueSaver(MemoryStore()),
    )
    host = AgentHost(GraphRunner(graph, config={"recursion_limit": 25}), stagger=FAST)
    try:
        report = await asyncio.wait_for(host.run(inputs(1)), 10)
    finally:
        await session.close()
    (agent,) = report.agents
    assert agent.status == "failed" and agent.error.startswith("GraphRecursionError")
    assert RUNS["simulate"] <= 2 * 25  # bounded, not run until the walltime


@pytest.mark.parametrize("suspend", [False, True], ids=["in-memory", "suspended"])
@pytest.mark.parametrize("limit, status", [(4, "failed"), (12, "done")])
async def test_a_limit_that_lets_an_agent_finish_in_memory_lets_it_finish_suspended(
    suspend, limit, status
):
    session = await thread_session()
    runtime = ToolRuntime(session)
    graph = react_graph(
        Scripted(script=simulate_times(5)),
        bind_tools(runtime, [task_tool(simulate, suspend=suspend)]),
        KeyValueSaver(MemoryStore()),
    )
    runner = GraphRunner(graph, config={"recursion_limit": limit})
    report = await AgentHost(runner, stagger=FAST).run(inputs(1))
    await session.close()
    (agent,) = report.agents
    assert agent.status == status, agent.error
    if status == "done":
        assert agent.suspensions == (5 if suspend else 0)
    assert not runner._resumes  # nothing kept for an agent that has ended


# --- keys of tool calls ---------------------------------------------------------


def numbered_per_message(turns: int, parallel: int = 1):
    """A model whose server numbers tool-call ids per message, as some do: each
    turn's calls are ``call-0``, ``call-1``, ..., with new arguments each turn.
    After ``turns`` turns it answers with every result, in order."""

    def script(messages):
        results = [m for m in messages if m.type == "tool"]
        turn = len(results) // parallel
        if turn < turns:
            return AIMessage(
                "",
                tool_calls=[
                    {"name": "simulate", "args": {"x": float(10 * turn + i)}, "id": f"call-{i}"}
                    for i in range(parallel)
                ],
            )
        return AIMessage(" | ".join(str(m.content) for m in results))

    return script


def expected_answer(turns: int, parallel: int = 1) -> str:
    return " | ".join(
        f'{{"y": {2.0 * (10 * turn + i)}}}' for turn in range(turns) for i in range(parallel)
    )


def tool_node_graph(model, tools, saver):
    """model -> one ToolNode task for all of a message's calls -> model."""
    from langgraph.prebuilt import tools_condition

    async def call_model(state: MessagesState):
        return {"messages": [await model.ainvoke(state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("model", call_model)
    g.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    g.add_edge(START, "model")
    g.add_conditional_edges("model", tools_condition)
    g.add_edge("tools", "model")
    return g.compile(checkpointer=saver)


def in_a_subgraph(model, tools, saver):
    """The react graph as a node of an outer graph, which checkpoints both."""
    g = StateGraph(MessagesState)
    g.add_node("agent", react_graph(model, tools))
    g.add_edge(START, "agent")
    return g.compile(checkpointer=saver)


@pytest.mark.parametrize("suspend", [False, True], ids=["in-memory", "suspended"])
@pytest.mark.parametrize(
    "shape, parallel",
    [("send", 1), ("send", 2), ("tool-node", 2), ("subgraph", 2)],
)
async def test_a_call_id_used_again_on_a_later_turn_is_a_new_call(suspend, shape, parallel):
    """A model server may number its tool calls per message, so a later turn
    reuses an earlier turn's ids. Each turn's calls run, once each, and each
    returns its own result; repeats on resume still run nothing again."""
    turns = 3
    session = await thread_session()
    runtime = ToolRuntime(session)
    tools = bind_tools(runtime, [task_tool(simulate, suspend=suspend)])
    model = Scripted(script=numbered_per_message(turns, parallel))
    build = {"send": react_graph, "tool-node": tool_node_graph, "subgraph": in_a_subgraph}[shape]
    graph = build(model, tools, KeyValueSaver(MemoryStore()))
    outputs = {}
    report = await AgentHost(
        GraphRunner(graph), stagger=FAST, on_done=lambda r, o: outputs.__setitem__(r.id, o)
    ).run(inputs(2))
    await session.close()
    assert report.finished == 2, [a.error for a in report.agents]
    for agent in report.agents:
        assert outputs[agent.id]["messages"][-1].content == expected_answer(turns, parallel)
        if not suspend:
            assert agent.suspensions == 0
        elif shape == "tool-node":  # one task for both calls: resumed once per call
            assert turns <= agent.suspensions <= turns * parallel
        else:
            assert agent.suspensions == turns
    assert RUNS == {"simulate": 2 * turns * parallel}
    assert runtime.stats()["submitted"] == 2 * turns * parallel


@pytest.mark.xfail(
    strict=True,
    reason="a node that loops between the model and its tools runs every turn in "
    "one LangGraph task, so a call id reused on a later turn shares the earlier key",
)
async def test_a_call_id_used_again_in_a_node_that_loops_itself_is_a_new_call():
    session = await thread_session()
    runtime = ToolRuntime(session)
    model = Scripted(script=numbered_per_message(turns=2))
    node = ToolNode(bind_tools(runtime, [task_tool(simulate)]), handle_tool_errors=True)

    async def agent(state: MessagesState):
        messages = list(state["messages"])
        while True:
            reply = await model.ainvoke(messages)
            messages.append(reply)
            if not reply.tool_calls:
                return {"messages": messages[len(state["messages"]) :]}
            messages.extend((await node.ainvoke({"messages": messages}))["messages"])

    g = StateGraph(MessagesState)
    g.add_node("agent", agent)
    g.add_edge(START, "agent")
    graph = g.compile(checkpointer=KeyValueSaver(MemoryStore()))
    outputs = {}
    await AgentHost(
        GraphRunner(graph), stagger=FAST, on_done=lambda r, o: outputs.__setitem__(r.id, o)
    ).run(inputs(1))
    await session.close()
    assert outputs["agent-0"]["messages"][-1].content == expected_answer(turns=2)


async def test_mcp_calls_with_a_reused_call_id_reach_the_service_as_new_calls():
    service = FakeService()
    runtime = ToolRuntime(service=service)
    lookup = mcp_tool("lookup", description="Look a material up.", args_schema=LookupArgs)

    def script(messages):
        done = sum(1 for m in messages if m.type == "tool")
        if done < 2:
            material = ["Fe", "Cu"][done]
            return AIMessage(
                "", tool_calls=[{"name": "lookup", "args": {"material": material}, "id": "call-0"}]
            )
        return AIMessage(" | ".join(str(m.content) for m in messages if m.type == "tool"))

    graph = react_graph(Scripted(script=script), bind_tools(runtime, [lookup]), KeyValueSaver(MemoryStore()))
    outputs = {}
    await AgentHost(
        GraphRunner(graph), stagger=FAST, on_done=lambda r, o: outputs.__setitem__(r.id, o)
    ).run(inputs(1))
    uids = [uid for uid, *_ in service.calls]
    assert [args["material"] for _, _, args, _ in service.calls] == ["Fe", "Cu"]
    assert len(set(uids)) == 2
    assert all(u.startswith("agent-0/") and u.endswith("/call-0") for u in uids)
    answer = outputs["agent-0"]["messages"][-1].content
    assert '"Fe"' in answer and '"Cu"' in answer


def decode_uid(uid: str) -> str:
    """The key a task uid was made from: the uid's encoding read back."""
    s = uid.removeprefix("flowgentic.")
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == ".":
            out.append("/")
            i += 1
        elif c == "_" and s[i + 1] == "_":
            out.append("_")
            i += 2
        elif c == "_":
            assert s[i + 1] == "x", uid
            end = s.index("_", i + 2)
            out.append(chr(int(s[i + 2 : end], 16)))
            i = end + 1
        else:
            assert c.isascii() and (c.isalnum() or c == "-"), uid
            out.append(c)
            i += 1
    return "".join(out)


def test_task_uids_are_one_to_one_and_readable():
    import random
    import re

    key = ToolRuntime.key
    pairs = [
        (key("a.b", "c"), key("a", "b.c")),
        (key("u 1", "c"), key("u_1", "c")),
        (key("a", "b/c"), key("a/b", "c")),
        (key("a", "c", "t"), key("a", "t/c")),
        (key("a", "c", "t"), key("a/t", "c")),
    ]
    for one, other in pairs:
        assert one != other
        assert ToolRuntime.uid(one) != ToolRuntime.uid(other)
    rng = random.Random(7)
    alphabet = "ab0-_./ %:|é"
    keys = {
        key(*("".join(rng.choices(alphabet, k=rng.randint(1, 6))) for _ in range(3)))
        for _ in range(5000)
    }
    uids = {ToolRuntime.uid(k) for k in keys}
    assert len(uids) == len(keys)
    for k in keys:
        uid = ToolRuntime.uid(k)
        assert re.fullmatch(r"flowgentic\.[A-Za-z0-9_.-]+", uid)
        assert decode_uid(uid) == k
    # Ordinary ids stay readable.
    assert key("agent-7", "call_ab12") == "agent-7/call_ab12"
    assert key("agent-7", "call-0", "1f0e-77aa") == "agent-7/1f0e-77aa/call-0"
    assert ToolRuntime.uid("agent-7/1f0e-77aa/call-0") == "flowgentic.agent-7.1f0e-77aa.call-0"
    assert ToolRuntime.uid("agent-7/call_ab12") == "flowgentic.agent-7.call__ab12"
