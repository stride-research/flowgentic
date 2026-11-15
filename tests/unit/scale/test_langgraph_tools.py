"""LangGraph agents in a host, with task, MCP and inline tools: suspension,
repeat safety, failures, order policies, and the checkpoints written."""

import asyncio

import pytest
from _support import (
    RUNS,
    Scripted,
    broken,
    react_graph,
    simulate,
    thread_session,
    two_tools_then_answer,
)
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from flowgentic.host import Agent, AgentHost, AgentState, Stagger
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


async def test_a_suspended_agent_resumes_and_nothing_runs_twice():
    session = await thread_session()
    runtime = ToolRuntime(session)
    tools = bind_tools(runtime, [task_tool(simulate, suspend=True), inline_note])
    store = MemoryStore()
    saver = KeyValueSaver(store)
    graph = react_graph(Scripted(script=two_tools_then_answer()), tools, saver)
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
    assert sum(1 for k in store.keys() if "/c/" in k) == 20  # keep_last=1
    final = outputs["agent-0"]["messages"][-1].content
    assert '{"y": 3.0}' in final and "noted hi" in final
    seconds = host.states.agent_seconds()
    assert seconds[AgentState.TOOL_WAIT] > 0


async def test_without_suspension_the_agent_waits_in_memory():
    session = await thread_session()
    runtime = ToolRuntime(session)
    saver = KeyValueSaver(MemoryStore())
    graph = react_graph(
        Scripted(script=two_tools_then_answer(extra=None)),
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
        Scripted(script=two_tools_then_answer(extra=None)),
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
        Scripted(script=two_tools_then_answer(sim="broken", extra=None)),
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


async def test_an_order_policy_decides_when_tasks_are_submitted():
    class Held:
        def __init__(self):
            self.gate = asyncio.Event()
            self.admitted: list[str] = []
            self.ended: list[str] = []

        async def admit(self, info: TaskInfo) -> None:
            await self.gate.wait()
            self.admitted.append(info.key)

        def done(self, info: TaskInfo) -> None:
            self.ended.append(info.key)

    session = await thread_session()
    order = Held()
    runtime = ToolRuntime(session, order=order)
    job = runtime.submit(task_tool(simulate), "a", "c1", {"x": 2.0})
    await asyncio.sleep(0.05)
    assert not job.done() and RUNS == {}
    order.gate.set()
    assert await job == {"y": 4.0}
    await session.close()
    assert order.admitted == order.ended == ["a/c1"]


class LookupArgs(BaseModel):
    material: str


class FakeService:
    def __init__(self):
        self.calls = []

    async def call(self, uid, tool, args):
        self.calls.append((uid, tool, args))
        if args["material"] == "bad":
            raise RuntimeError("server gone")
        return {"material": args["material"], "score": 0.5}


async def test_mcp_tools_go_through_the_service():
    service = FakeService()
    runtime = ToolRuntime(service=service)
    lookup = mcp_tool("lookup", description="Look a material up.", args_schema=LookupArgs)
    (tool_,) = bind_tools(runtime, [lookup])
    assert "tool_call_id" not in tool_.tool_call_schema.model_json_schema()["properties"]
    call = {"name": "lookup", "args": {"material": "x"}, "id": "c9", "type": "tool_call"}
    out = await tool_.ainvoke(call, {"configurable": {"thread_id": "agent-7"}})
    assert service.calls == [("agent-7/c9", "lookup", {"material": "x"})]
    assert '"score": 0.5' in out.content
    bad = await tool_.ainvoke(
        {**call, "args": {"material": "bad"}}, {"configurable": {"thread_id": "agent-7"}}
    )
    assert "lookup failed: RuntimeError: server gone" in bad.content


async def test_many_agents_in_one_host():
    session = await thread_session(workers=16)
    runtime = ToolRuntime(session)
    graph = react_graph(
        Scripted(script=two_tools_then_answer()),
        bind_tools(runtime, [task_tool(simulate, suspend=True), inline_note]),
        KeyValueSaver(MemoryStore()),
    )
    report = await AgentHost(GraphRunner(graph), max_live_agents=200, stagger=FAST).run(
        inputs(1000)
    )
    await session.close()
    assert report.finished == 1000
    assert RUNS == {"simulate": 1000, "inline_note": 1000}


def write_result(x: float, key: str, scale: int) -> dict:
    """What the worker runs: it needs a key and a scale the model never sees."""
    RUNS["write_result"] = RUNS.get("write_result", 0) + 1
    return {"key": key, "y": scale * x, "t_start": 1.0, "t_end": 2.0}


class SimArgs(BaseModel):
    x: float


async def test_the_model_and_the_worker_see_different_things_and_budgets_refuse():
    class Budget:
        def __init__(self, most):
            self.most, self.used = most, {}

        async def admit(self, info):
            n = self.used.get(info.agent_id, 0)
            if n >= self.most:
                raise RuntimeError(f"budget spent: at most {self.most} per agent")
            self.used[info.agent_id] = n + 1

        def done(self, info):
            pass

    session = await thread_session()
    runtime = ToolRuntime(session, order=Budget(most=1))
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
    out = await tool_.ainvoke({"name": "simulate", "args": {"x": 2.0}, "id": "c1", "type": "tool_call"}, config)
    assert out.content == '{"result_key": "res/agent-1/c1"}'
    refused = await tool_.ainvoke({"name": "simulate", "args": {"x": 3.0}, "id": "c2", "type": "tool_call"}, config)
    assert "budget spent: at most 1 per agent" in refused.content
    await session.close()
    assert RUNS == {"write_result": 1}
    ok, no = records
    assert ok.ok and ok.value["y"] == 20.0 and ok.info.kwargs["key"] == "res/agent-1/c1"
    assert ok.t_submit <= ok.t_admit <= ok.t_end
    assert not no.ok and no.t_admit is None and "budget spent" in no.error
