"""LangGraph agents in an agent host: suspension through a tool's interrupt,
each tool call as its own task, foreign interrupts, run configuration, and a
suspended run freed when it stops."""

import asyncio
import gc
from typing import Annotated

import pytest
from _support import Scripted, calls_then_answer, react_graph
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END
from langgraph.types import interrupt

from flowgentic.host import Agent, AgentHost, AgentState, Finished, Interrupted, Stagger, Suspended, register_pending
from flowgentic.langgraph import PENDING_KEY, GraphRunner, record_interrupt, route_tool_calls

FAST = Stagger(batch=50, interval_s=0.0)
RUNS: dict[str, int] = {}
TASKS: dict[str, asyncio.Future] = {}


@pytest.fixture(autouse=True)
def clear():
    RUNS.clear()
    TASKS.clear()


def count(name: str) -> None:
    RUNS[name] = RUNS.get(name, 0) + 1


@tool
async def slow_task(x: float, config: RunnableConfig, call_id: Annotated[str, InjectedToolCallId]) -> str:
    """A task the agent suspends on, submitted once per call."""
    key = f"{config['configurable']['thread_id']}/{call_id}"
    future = TASKS.get(key)
    if future is None:  # submitted once: a resumed agent finds it
        count("submitted")
        future = TASKS[key] = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_later(0.02, future.set_result, 2 * x)
        register_pending(key, future)
    try:
        interrupt({PENDING_KEY: key})
    except GraphInterrupt as exc:
        record_interrupt(exc)  # freed when the run stops
        raise
    return f"result {await future}"


@tool
def note(text: str) -> str:
    """Take a note."""
    count("note")
    return f"noted {text}"


@tool
def ask_a_human(question: str) -> str:
    """Ask a human."""
    return interrupt({"question": question})


def inputs(n: int):
    return [Agent(f"agent-{i}", {"messages": [HumanMessage("go")]}) for i in range(n)]


async def test_agents_suspend_resume_and_no_tool_runs_twice():
    model = Scripted(script=calls_then_answer(("slow_task", {"x": 1.5}), ("note", {"text": "hi"})))
    graph = react_graph(model, [slow_task, note], InMemorySaver())
    outputs = {}
    host = AgentHost(GraphRunner(graph), stagger=FAST, on_done=lambda r, out: outputs.__setitem__(r.id, out))
    seen = set()

    async def watch():
        while len(outputs) < 20:
            seen.update(host.states.counts())
            seen.update(s for s, c in host.states.counts().items() if c)
            await asyncio.sleep(0.005)

    report, _ = await asyncio.gather(host.run(inputs(20)), watch())
    assert report.finished == 20
    assert all(a.suspensions == 1 for a in report.agents)
    # The task was submitted once and the note taken once per agent: the
    # resumed run ran only the call that suspended.
    assert RUNS == {"submitted": 20, "note": 20}
    final = outputs["agent-0"]["messages"][-1].content
    assert "result 3.0" in final and "noted hi" in final
    assert host.states.agent_seconds()[AgentState.TOOL_WAIT] > 0


async def test_a_foreign_interrupt_ends_the_agent_as_interrupted():
    model = Scripted(script=calls_then_answer(("ask_a_human", {"question": "go on?"})))
    runner = GraphRunner(react_graph(model, [ask_a_human], InMemorySaver()))
    step = await runner.start(Agent("a", {"messages": [HumanMessage("go")]}))
    assert isinstance(step, Interrupted)
    assert step.interrupts[0].value == {"question": "go on?"}


async def test_run_configuration_is_merged_under_the_agents():
    seen = {}

    @tool
    def peek(config: RunnableConfig) -> str:
        """Look at the run configuration."""
        seen.update(config["configurable"])
        seen["recursion_limit"] = config["recursion_limit"]
        return "ok"

    model = Scripted(script=calls_then_answer(("peek", {})))
    runner = GraphRunner(
        react_graph(model, [peek], InMemorySaver()),
        config={"recursion_limit": 50, "configurable": {"site": "a", "thread_id": "ignored"}},
    )
    agent = Agent("agent-7", {"messages": [HumanMessage("go")]}, config={"configurable": {"site": "b"}})
    assert isinstance(await runner.start(agent), Finished)
    assert seen["thread_id"] == "agent-7" and seen["site"] == "b" and seen["recursion_limit"] == 50


def test_each_tool_call_goes_out_as_its_own_task():
    calls = [{"name": "note", "args": {"text": str(i)}, "id": f"c{i}"} for i in range(3)]
    message = AIMessage("", tool_calls=calls)
    sends = route_tool_calls({"messages": [message]})
    assert [(s.node, s.arg) for s in sends] == [("tools", [c]) for c in message.tool_calls]
    assert route_tool_calls({"messages": [AIMessage("done")]}) == END
    assert route_tool_calls({"messages": []}) == END


@pytest.mark.parametrize(
    "breakpoint", [{"interrupt_before": ["b"]}, {"interrupt_after": ["a"]}, {"interrupt_before": "*"}]
)
def test_a_graph_with_static_breakpoints_is_refused(breakpoint):
    """A static breakpoint stops a run without an interrupt in its output, so
    the host would report the stopped agent as finished, with partial output."""
    from langgraph.graph import START, MessagesState, StateGraph

    g = StateGraph(MessagesState)
    g.add_node("a", lambda s: {"messages": [("ai", "a")]})
    g.add_node("b", lambda s: {"messages": [("ai", "b")]})
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    graph = g.compile(checkpointer=InMemorySaver(), **breakpoint)
    with pytest.raises(ValueError, match="interrupt_before or interrupt_after"):
        GraphRunner(graph)
    assert isinstance(GraphRunner(g.compile(checkpointer=InMemorySaver())), GraphRunner)


def test_route_tool_calls_is_the_only_router():
    import flowgentic.langgraph as lg

    assert "tool_call_router" not in lg.__all__ and not hasattr(lg, "tool_call_router")


def test_the_asyncflow_integration_spells_langgraph():
    import flowgentic.langgraph as lg

    assert {"LangGraphIntegration", "ExecutionWrappersLangGraph"} <= set(lg.__all__)
    assert not {"LangraphIntegration", "ExecutionWrappersLangraph"} & set(lg.__all__)
    assert lg.LangGraphIntegration.__name__ == "LangGraphIntegration"
    assert lg.ExecutionWrappersLangGraph.__name__ == "ExecutionWrappersLangGraph"


@tool
async def stuck_task(x: float, config: RunnableConfig, call_id: Annotated[str, InjectedToolCallId]) -> str:
    """A task that never ends, or, for x < 0, one the host cannot find."""
    key = f"{config['configurable']['thread_id']}/{call_id}"
    if x >= 0 and key not in TASKS:
        TASKS[key] = asyncio.get_running_loop().create_future()
        register_pending(key, TASKS[key])
    try:
        interrupt({PENDING_KEY: key})
    except GraphInterrupt as exc:
        record_interrupt(exc)
        raise
    return "never"


def slow_then(second: str, x: float):
    """One slow task, then ``second``: the agent suspends twice."""

    def script(messages):
        done = sum(1 for m in messages if m.type == "tool")
        name, args = ("slow_task", {"x": 1.0}) if done == 0 else (second, {"x": x})
        return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"c{done}"}])

    return script


@pytest.mark.parametrize("ending", ["cancelled", "failed"])
async def test_an_agent_that_ends_while_suspended_leaves_no_resume_count(ending):
    """An agent cancelled, or failed by the host, while it waits between two
    resumes never reaches the runner again: its count of resumes goes with it."""
    x = 1.0 if ending == "cancelled" else -1.0
    model = Scripted(script=slow_then("stuck_task", x))
    runner = GraphRunner(react_graph(model, [slow_task, stuck_task], InMemorySaver()))
    host = AgentHost(runner, stagger=FAST)
    run = asyncio.create_task(host.run(inputs(3)))
    if ending == "cancelled":
        while len(TASKS) < 6:  # each agent waits on its second task
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.01)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
    else:
        report = await run
        assert [a.status for a in report.agents] == ["failed"] * 3
        assert all("did not submit" in a.error for a in report.agents)
    assert not runner._resumes


@tool
def fail(text: str) -> str:
    """A tool that raises."""
    raise RuntimeError(f"no {text}")


def threads_in(store) -> set[str]:
    return {key.split("/")[1] for key in store.keys()}


async def test_a_finished_agents_thread_leaves_the_store():
    """Each finished agent's last checkpoint and pending writes are deleted, so
    the store holds the agents still running, not the campaign's."""
    from flowgentic.langgraph import KeyValueSaver
    from flowgentic.store import MemoryStore

    store = MemoryStore()
    model = Scripted(script=calls_then_answer(("slow_task", {"x": 1.5})))
    outputs = {}
    runner = GraphRunner(react_graph(model, [slow_task], KeyValueSaver(store)))
    host = AgentHost(runner, stagger=FAST, on_done=lambda r, out: outputs.__setitem__(r.id, out))
    report = await host.run(inputs(20))
    assert report.finished == 20 and all(a.suspensions == 1 for a in report.agents)
    assert "result 3.0" in outputs["agent-7"]["messages"][-1].content
    assert store.keys() == []


@pytest.mark.parametrize("delete_finished", [True, False])
async def test_interrupted_and_failed_agents_keep_their_threads(delete_finished):
    """A human-in-the-loop interrupt resumes from the thread, and a failed
    agent's thread is kept to debug or retry it. With delete_finished=False,
    a finished agent's is kept too."""
    from langgraph.types import Command

    from flowgentic.langgraph import KeyValueSaver
    from flowgentic.store import MemoryStore

    store = MemoryStore()
    saver = KeyValueSaver(store)

    calls = {
        "ask": ("ask_a_human", {"question": "go?"}),
        "fail": ("fail", {"text": "x"}),
        "wait": ("slow_task", {"x": 1.0}),
    }

    def script(messages):
        text = messages[0].content
        if text == "done" or any(m.type == "tool" for m in messages):
            return AIMessage("done")
        name, args = calls[text]
        return AIMessage("", tool_calls=[{"name": name, "args": args, "id": "c0"}])

    graph = react_graph(
        Scripted(script=script), [ask_a_human, fail, slow_task], saver, handle_tool_errors=None
    )
    runner = GraphRunner(graph, delete_finished=delete_finished)
    agents = [Agent(text, {"messages": [HumanMessage(text)]}) for text in ("ask", "fail", "wait", "done")]
    report = await AgentHost(runner, stagger=FAST).run(agents)
    assert {a.id: a.status for a in report.agents} == {
        "ask": "interrupted",
        "fail": "failed",
        "wait": "done",
        "done": "done",
    }
    kept = {"ask", "fail"} | (set() if delete_finished else {"wait", "done"})
    assert threads_in(store) == kept
    # The interrupted agent still resumes from its thread.
    config = {"configurable": {"thread_id": "ask"}}
    out = await graph.ainvoke(Command(resume="yes"), config)
    assert out["messages"][-1].content == "done"


async def test_a_thread_that_cannot_be_deleted_leaves_the_agent_finished(caplog):
    class Undeletable(InMemorySaver):
        async def adelete_thread(self, thread_id):
            raise ConnectionError("store unreachable")

    model = Scripted(script=calls_then_answer(("note", {"text": "hi"})))
    runner = GraphRunner(react_graph(model, [note], Undeletable()))
    with caplog.at_level("DEBUG", logger="flowgentic.langgraph.runner"):
        report = await AgentHost(runner, stagger=FAST).run(inputs(3))
    assert report.finished == 3
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "store unreachable" in warnings[0].getMessage()


@pytest.mark.filterwarnings("ignore:`durability` has no effect")
async def test_a_graph_without_a_checkpointer_runs_to_its_end():
    model = Scripted(script=calls_then_answer(("note", {"text": "hi"})))
    runner = GraphRunner(react_graph(model, [note]))
    assert (await AgentHost(runner, stagger=FAST).run(inputs(2))).finished == 2


def left_by(run_objects: tuple) -> list:
    return [type(o).__name__ for o in gc.get_objects() if isinstance(o, run_objects)]


@pytest.mark.parametrize(
    "calls",
    [
        1,
        pytest.param(
            2,
            marks=pytest.mark.xfail(
                strict=True,
                reason="LangGraph's runner combines a step's interrupts into an "
                "exception of its own, which keeps its frames in a cycle",
            ),
        ),
    ],
)
async def test_a_suspension_leaves_nothing_for_the_collector(calls):
    """A suspended agent's run is freed when it stops, not at the collector's
    next pass: with thousands of agents per process, what each suspension left
    behind filled the collector's young generations."""
    from langgraph.pregel._runner import PregelRunner

    model = Scripted(script=calls_then_answer(*[("slow_task", {"x": 1.0 + i}) for i in range(calls)]))
    runner = GraphRunner(react_graph(model, [slow_task], InMemorySaver()))
    agent = Agent("agent-0", {"messages": [HumanMessage("go")]})
    gc.collect()
    gc.disable()
    try:
        step = await runner.start(agent)
        assert isinstance(step, Suspended) and len(step.waits) == calls
        left = left_by((GraphInterrupt, PregelRunner))
        await asyncio.wait([TASKS[key] for _, key in step.waits])
        done = {rid: {"flowgentic_done": key} for rid, key in step.waits}
        step = await runner.resume(agent, done)
        assert isinstance(step, Finished)
        left_at_end = left_by((GraphInterrupt, PregelRunner))
    finally:
        gc.enable()
    assert left == []
    assert left_at_end == []
