"""Flowgentic's own agent loop in an agent host: suspension with its state in a
key-value store, concurrent tool calls, tool errors returned to the model, the
model-call limit, structured answers, the history-trim hook, interrupts, and
the accounting and telemetry the host's other parts use."""

import asyncio
import json
import time
from collections import Counter
from typing import Any

import pytest
from _support import RUNS, Scripted, broken, calls_then_answer, simulate, thread_session
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from flowgentic.fleet import Fleet
from flowgentic.host import Agent, AgentHost, AgentState, Stagger
from flowgentic.loop import (
    Interrupt,
    Interruption,
    LoopOutput,
    LoopRunner,
    ModelCallLimitExceeded,
)
from flowgentic.store import MemoryStore
from flowgentic.tools import ToolRuntime, mcp_tool, task_tool

FAST = Stagger(batch=50, interval_s=0.0)


@pytest.fixture(autouse=True)
def clear_runs():
    RUNS.clear()


class CountingStore(MemoryStore):
    """A ``MemoryStore`` that counts its calls and keeps the last value put."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: Counter[str] = Counter()
        self.last_put: Any = None

    def get(self, key, *default):
        self.calls["get"] += 1
        return super().get(key, *default)

    def put(self, key, value):
        self.calls["put"] += 1
        self.last_put = value
        super().put(key, value)

    def delete(self, key):
        self.calls["delete"] += 1
        super().delete(key)


class Slow(Scripted):
    """A scripted model that takes ``latency_s`` to answer."""

    latency_s: float = 0.01

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        await asyncio.sleep(self.latency_s)
        return await super()._agenerate(messages, stop, run_manager, **kwargs)


class Binding(Scripted):
    """A scripted model that records the tools it is bound to."""

    bound: list = []

    def bind_tools(self, tools, **kwargs):
        self.bound.extend(tools)
        return self


@tool
def inline_note(text: str) -> str:
    """Take a note."""
    RUNS["inline_note"] = RUNS.get("inline_note", 0) + 1
    return f"noted {text}"


@tool
async def wait_a_bit(name: str) -> str:
    """Wait for 0.2 s."""
    await asyncio.sleep(0.2)
    return name


@tool
def fragile(x: int) -> str:
    """Fail on odd numbers."""
    if x % 2:
        raise ValueError(f"odd {x}")
    return "even"


@tool
def ask_a_human(question: str) -> str:
    """Ask a human."""
    raise Interrupt({"question": question})


def slow_simulate(x: float) -> dict:
    """A simulation that takes a while."""
    time.sleep(0.5)
    return {"y": 2 * x}


class LookupArgs(BaseModel):
    material: str


class FakeService:
    def __init__(self):
        self.calls = []

    async def call(self, uid, tool, args, *, idempotent=False):
        self.calls.append((uid, tool, args, idempotent))
        return {"material": args["material"], "score": 0.5}


class Verdict(BaseModel):
    """The agent's verdict on its candidate."""

    score: float
    keep: bool


def inputs(n: int, prefix: str = "agent") -> list[Agent]:
    return [Agent(f"{prefix}-{i}", [HumanMessage("go")]) for i in range(n)]


def turns(messages: list[BaseMessage]) -> int:
    """The model's replies so far."""
    return sum(1 for m in messages if m.type == "ai")


def collect(outputs: dict):
    return lambda result, output: outputs.__setitem__(result.id, output)


# --- suspension and repeat safety ---------------------------------------------------


async def test_a_suspended_agent_resumes_from_the_store_and_nothing_runs_twice():
    session = await thread_session()
    runtime = ToolRuntime(session)
    store = CountingStore()
    model = Scripted(
        script=calls_then_answer(("simulate", {"x": 1.5}), ("inline_note", {"text": "hi"}))
    )
    runner = LoopRunner(
        model, [task_tool(simulate, suspend=True), inline_note], runtime=runtime, store=store
    )
    outputs = {}
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(20))
    await session.close()

    assert report.finished == 20
    assert all(a.suspensions == 1 for a in report.agents)
    assert RUNS == {"simulate": 20, "inline_note": 20}  # each ran once per agent
    assert runtime.stats()["repeats"] == 20  # the resume found the task
    # One put at the suspension, one get at the resume, one delete at the end.
    assert store.calls == {"put": 20, "get": 20, "delete": 20}
    assert store.keys() == []
    out = outputs["agent-0"]
    assert isinstance(out, LoopOutput)
    assert out.model_calls == 2 and out.structured_response is None
    final = out.messages[-1].content
    assert '{"y": 3.0}' in final and "noted hi" in final
    # The note answered first, the task after the resume; the history keeps
    # the order of the calls.
    assert [m.tool_call_id for m in out.messages if m.type == "tool"] == ["call-0", "call-1"]
    stats = runner.stats()
    assert stats["model_calls"] == 40 and stats["suspensions"] == 20
    assert stats["tool_calls"] == 40 and stats["tool_errors"] == 0
    assert (stats["store_puts"], stats["store_gets"], stats["store_deletes"]) == (20, 20, 20)


async def test_an_agent_that_never_suspends_never_touches_the_store():
    store = CountingStore()
    model = Scripted(script=calls_then_answer(("inline_note", {"text": "hi"})))
    report = await AgentHost(
        LoopRunner(model, [inline_note], store=store), stagger=FAST
    ).run(inputs(5))
    assert report.finished == 5
    assert store.calls == {}


async def test_a_call_id_reused_in_a_later_turn_is_a_new_task():
    """A model server that numbers its calls per reply gives every turn's first
    call the same id; each turn's call is still a task of its own."""
    session = await thread_session()
    runtime = ToolRuntime(session)

    def script(messages):
        if turns(messages) < 2:
            x = float(turns(messages) + 1)
            return AIMessage("", tool_calls=[{"name": "simulate", "args": {"x": x}, "id": "call-0"}])
        return AIMessage(" | ".join(m.content for m in messages if m.type == "tool"))

    outputs = {}
    runner = LoopRunner(
        Scripted(script=script), [task_tool(simulate, suspend=True)], runtime=runtime
    )
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(3))
    await session.close()
    assert report.finished == 3
    assert outputs["agent-0"].messages[-1].content == '{"y": 2.0} | {"y": 4.0}'
    assert RUNS == {"simulate": 6}
    assert runtime.stats()["submitted"] == 6


async def test_a_task_tool_that_does_not_suspend_is_awaited_in_the_loop():
    session = await thread_session()
    runtime = ToolRuntime(session)
    store = CountingStore()
    model = Scripted(script=calls_then_answer(("simulate", {"x": 2.0})))
    outputs = {}
    report = await AgentHost(
        LoopRunner(model, [task_tool(simulate)], runtime=runtime, store=store),
        stagger=FAST,
        on_done=collect(outputs),
    ).run(inputs(4))
    await session.close()
    assert report.finished == 4
    assert all(a.suspensions == 0 for a in report.agents)
    assert outputs["agent-0"].messages[-1].content == '{"y": 4.0}'
    assert store.calls == {}


async def test_mcp_tools_go_through_the_service_keyed_by_agent_and_turn():
    service = FakeService()
    lookup = mcp_tool(
        "lookup", description="Look a material up.", args_schema=LookupArgs, idempotent=True
    )
    model = Scripted(script=calls_then_answer(("lookup", {"material": "x"})))
    outputs = {}
    runner = LoopRunner(model, [lookup], runtime=ToolRuntime(service=service))
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(1))
    assert report.finished == 1
    assert service.calls == [("agent-0/1/call-0", "lookup", {"material": "x"}, True)]
    assert '"score": 0.5' in outputs["agent-0"].messages[-1].content


@pytest.mark.parametrize("setup", ["task, no session", "mcp, no service"])
async def test_a_runtime_set_up_wrong_fails_the_agent(setup):
    """As with LangGraph's tools: a misconfigured campaign fails its agents
    rather than tell the model that the call failed."""
    if setup.startswith("task"):
        tool_, call = task_tool(simulate), ("simulate", {"x": 1.0})
        expected = "task tools need a ToolRuntime with a RHAPSODY session"
    else:
        tool_ = mcp_tool("lookup", description="Look up.", args_schema=LookupArgs)
        call = ("lookup", {"material": "x"})
        expected = "need a ToolRuntime with a tool service"
    runner = LoopRunner(Scripted(script=calls_then_answer(call)), [tool_], runtime=ToolRuntime())
    report = await AgentHost(runner, stagger=FAST).run(inputs(1))
    (agent,) = report.agents
    assert agent.status == "failed" and agent.error.startswith("RuntimeError: ")
    assert expected in agent.error


# --- concurrency ----------------------------------------------------------------


async def test_the_tool_calls_of_one_reply_run_at_once():
    model = Scripted(
        script=calls_then_answer(
            ("wait_a_bit", {"name": "a"}), ("wait_a_bit", {"name": "b"}), ("wait_a_bit", {"name": "c"})
        )
    )
    outputs = {}
    t = time.perf_counter()
    report = await AgentHost(
        LoopRunner(model, [wait_a_bit]), stagger=FAST, on_done=collect(outputs)
    ).run(inputs(1))
    assert time.perf_counter() - t < 0.5  # three 0.2 s calls, at once
    assert report.finished == 1
    assert outputs["agent-0"].messages[-1].content == "a | b | c"


# --- errors -----------------------------------------------------------------------


async def test_tool_errors_reach_the_model_and_the_agent_goes_on():
    session = await thread_session()
    runtime = ToolRuntime(session)
    seen = []

    def script(messages):
        results = [m for m in messages if m.type == "tool"]
        if results:
            seen.extend(results)
            return AIMessage("done")
        return AIMessage(
            "",
            tool_calls=[
                {"name": "fragile", "args": {"x": 1}, "id": "a"},  # raises
                {"name": "fragile", "args": {"x": "one"}, "id": "b"},  # invalid arguments
                {"name": "nowhere", "args": {}, "id": "c"},  # no such tool
                {"name": "broken", "args": {"x": 1.0}, "id": "d"},  # the task fails
                {"name": "simulate", "args": {}, "id": "e"},  # a task's argument missing
            ],
            invalid_tool_calls=[
                {"name": "fragile", "args": "{x: ", "id": "f", "error": "bad JSON", "type": "invalid_tool_call"}
            ],
        )

    runner = LoopRunner(
        Scripted(script=script), [fragile, task_tool(broken), task_tool(simulate)], runtime=runtime
    )
    report = await AgentHost(runner, stagger=FAST).run(inputs(1))
    await session.close()

    assert report.finished == 1
    by_id = {m.tool_call_id: m for m in seen}
    assert [m.tool_call_id for m in seen] == list("abcdef")
    assert all(m.status == "error" for m in seen)
    assert by_id["a"].content == "Error: ValueError('odd 1')\n Please fix your mistakes."
    assert "ValidationError" in by_id["b"].content and "x" in by_id["b"].content
    assert by_id["c"].content.startswith("Error: nowhere is not a valid tool, try one of [")
    assert by_id["d"].content == "broken failed: ValueError: no convergence at 1.0"
    assert "ValidationError" in by_id["e"].content and "simulate" not in RUNS  # never submitted
    assert "bad JSON" in by_id["f"].content
    assert runner.stats()["tool_errors"] == 6


async def test_an_agent_that_never_answers_fails_at_its_model_call_limit():
    """The count of model calls is saved with the agent's state, so the limit
    holds across suspensions."""
    session = await thread_session()
    runtime = ToolRuntime(session)
    store = CountingStore()

    def script(messages):
        n = turns(messages)
        return AIMessage("", tool_calls=[{"name": "simulate", "args": {"x": 1.0}, "id": f"c{n}"}])

    runner = LoopRunner(
        Scripted(script=script),
        [task_tool(simulate, suspend=True)],
        runtime=runtime,
        store=store,
        max_model_calls=3,
    )
    report = await AgentHost(runner, stagger=FAST).run(inputs(2))
    await session.close()

    assert [a.status for a in report.agents] == ["failed", "failed"]
    assert report.errors == {"ModelCallLimitExceeded": 2}
    assert "3 model calls" in report.agents[0].error
    assert all(a.suspensions == 3 for a in report.agents)
    assert RUNS == {"simulate": 6}
    assert store.keys() == []  # a failed agent leaves no state behind


async def test_a_model_call_limit_below_one_is_refused():
    with pytest.raises(ValueError, match="max_model_calls"):
        LoopRunner(Scripted(script=lambda m: AIMessage("hi")), max_model_calls=0)


def test_the_limit_error_is_a_runtime_error():
    assert issubclass(ModelCallLimitExceeded, RuntimeError)


async def test_an_agent_that_fails_after_a_suspension_leaves_no_state():
    session = await thread_session()
    runtime = ToolRuntime(session)
    store = CountingStore()

    def script(messages):
        if turns(messages) == 0:
            return AIMessage("", tool_calls=[{"name": "simulate", "args": {"x": 1.0}, "id": "c"}])
        raise ConnectionError("model server gone")

    runner = LoopRunner(
        Scripted(script=script), [task_tool(simulate, suspend=True)], runtime=runtime, store=store
    )
    report = await AgentHost(runner, stagger=FAST).run(inputs(3))
    await session.close()
    assert report.errors == {"ConnectionError": 3}
    assert store.calls == {"put": 3, "get": 3, "delete": 3}
    assert store.keys() == []


async def test_cancelling_the_host_while_agents_wait_leaves_the_store_empty(monkeypatch):
    session = await thread_session(workers=4)
    runtime = ToolRuntime(session)
    store = CountingStore()
    forgotten = []
    monkeypatch.setattr(Fleet, "agent_done", lambda self, agent: forgotten.append(agent))
    fleet = Fleet([Scripted(script=calls_then_answer(("slow_simulate", {"x": 1.0})))])
    runner = LoopRunner(
        fleet, [task_tool(slow_simulate, suspend=True)], runtime=runtime, store=store
    )
    host = AgentHost(runner, stagger=FAST)
    run = asyncio.create_task(host.run(inputs(4)))
    while len(store.keys()) < 4:
        await asyncio.sleep(0.01)
    assert sorted(store.keys()) == [f"loop/agent-{i}" for i in range(4)]
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    await session.close()
    assert store.keys() == []
    assert sorted(forgotten) == [f"agent-{i}" for i in range(4)]


# --- structured answers -------------------------------------------------------------


async def test_a_structured_answer_is_validated_and_ends_the_agent():
    def script(messages):
        n = turns(messages)
        if n == 0:  # the answer's arguments are wrong
            return AIMessage("", tool_calls=[{"name": "Verdict", "args": {"score": "high"}, "id": "v1"}])
        if n == 1:  # an answer beside another call
            return AIMessage(
                "",
                tool_calls=[
                    {"name": "Verdict", "args": {"score": 0.9, "keep": True}, "id": "v2"},
                    {"name": "inline_note", "args": {"text": "x"}, "id": "n1"},
                ],
            )
        if n == 2:  # an answer in prose
            return AIMessage("It looks good to me.")
        return AIMessage("", tool_calls=[{"name": "Verdict", "args": {"score": 0.9, "keep": True}, "id": "v3"}])

    model = Binding(script=script, bound=[])
    outputs = {}
    runner = LoopRunner(model, [inline_note], response_format=Verdict)
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(1))

    assert report.finished == 1
    out = outputs["agent-0"]
    assert out.structured_response == Verdict(score=0.9, keep=True)
    assert out.model_calls == 4
    assert [getattr(t, "name", getattr(t, "__name__", None)) for t in model.bound] == [
        "inline_note",
        "Verdict",
    ]
    tools = {m.tool_call_id: m for m in out.messages if m.type == "tool"}
    assert tools["v1"].status == "error" and "score" in tools["v1"].content
    assert tools["v2"].status == "error" and "on its own" in tools["v2"].content
    assert tools["n1"].content == "noted x"  # the other call ran
    nudge = [m for m in out.messages if m.type == "human"][-1]
    assert "Verdict" in nudge.content  # asked to answer through the tool
    assert tools["v3"].status == "success" and out.messages[-1] is tools["v3"]
    assert RUNS == {"inline_note": 1}


async def test_a_structured_answer_given_as_json_text_is_accepted():
    model = Scripted(script=lambda m: AIMessage(json.dumps({"score": 0.5, "keep": False})))
    outputs = {}
    runner = LoopRunner(model, response_format=Verdict)
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(1))
    assert report.finished == 1
    assert outputs["agent-0"].structured_response == Verdict(score=0.5, keep=False)
    assert outputs["agent-0"].model_calls == 1


# --- the history ----------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_the_trim_hook_shapes_what_the_model_sees_and_what_is_saved(kind):
    session = await thread_session()
    runtime = ToolRuntime(session)
    store = CountingStore()
    seen = []

    def script(messages):
        seen.append([m.type for m in messages])
        if len(seen) < 4:  # three turns of tools, then the answer
            return AIMessage("", tool_calls=[{"name": "simulate", "args": {"x": 1.0}, "id": f"c{len(seen)}"}])
        return AIMessage("done")

    def keep_last_turn(messages):
        return messages[:1] + messages[-2:] if len(messages) > 3 else messages

    async def keep_last_turn_async(messages):
        return keep_last_turn(messages)

    trim = keep_last_turn if kind == "sync" else keep_last_turn_async
    runner = LoopRunner(
        Scripted(script=script),
        [task_tool(simulate, suspend=True)],
        runtime=runtime,
        store=store,
        system_prompt="Be brief.",
        trim=trim,
        max_model_calls=10,
    )
    outputs = {}
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(1))
    await session.close()

    assert report.finished == 1
    # The system prompt goes with every call, and is never trimmed or saved.
    assert seen == [
        ["system", "human"],
        ["system", "human", "ai", "tool"],
        ["system", "human", "ai", "tool"],
        ["system", "human", "ai", "tool"],
    ]
    saved = store.last_put["messages"]
    assert [m.type for m in saved] == ["human", "ai", "tool", "ai"]  # trimmed, then the call
    assert [m.type for m in outputs["agent-0"].messages] == ["human", "ai", "tool", "ai"]
    assert outputs["agent-0"].model_calls == 4  # trimming keeps the count


async def test_inputs_in_every_form_and_the_callers_list_unchanged():
    given = [HumanMessage("go")]
    agents = [
        Agent("a", "go"),
        Agent("b", given),
        Agent("c", {"messages": [("human", "go")]}),
        Agent("d", HumanMessage("go")),
    ]
    model = Scripted(script=lambda m: AIMessage(f"{len(m)} {m[-1].content}"))
    outputs = {}
    report = await AgentHost(
        LoopRunner(model, system_prompt="Be brief."), stagger=FAST, on_done=collect(outputs)
    ).run(agents)
    assert report.finished == 4
    for out in outputs.values():
        assert [(m.type, m.content) for m in out.messages] == [("human", "go"), ("ai", "2 go")]
    assert given == [HumanMessage("go")]


# --- interrupts -------------------------------------------------------------------


async def test_a_tool_that_interrupts_ends_its_agent_as_interrupted():
    model = Scripted(
        script=calls_then_answer(("ask_a_human", {"question": "go on?"}), ("inline_note", {"text": "hi"}))
    )
    store = CountingStore()
    outputs = {}
    runner = LoopRunner(model, [ask_a_human, inline_note], store=store)
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(2))

    assert [a.status for a in report.agents] == ["interrupted", "interrupted"]
    (stop,) = report.agents[0].interrupts
    assert isinstance(stop, Interruption)
    assert stop.value == {"question": "go on?"}
    assert stop.tool_call["id"] == "call-0"
    # The note answered; the question did not.
    assert [m.type for m in stop.messages] == ["human", "ai", "tool"]
    assert stop.messages[-1].tool_call_id == "call-1"
    assert stop.model_calls == 1
    assert outputs == {"agent-0": None, "agent-1": None}
    assert RUNS == {"inline_note": 2}
    assert store.calls == {} and store.keys() == []
    assert runner.stats()["interrupts"] == 2


# --- models, accounting, telemetry -------------------------------------------------


async def test_a_fleet_takes_the_agents_calls_and_hears_when_they_end(monkeypatch):
    forgotten = []
    monkeypatch.setattr(Fleet, "agent_done", lambda self, agent: forgotten.append(agent))
    script = calls_then_answer(("inline_note", {"text": "hi"}))
    fleet = Fleet([Scripted(script=script), Scripted(script=script)])
    report = await AgentHost(LoopRunner(fleet, [inline_note]), stagger=FAST).run(inputs(10))
    assert report.finished == 10
    assert fleet.stats()["calls"] == 20
    assert sorted(forgotten) == sorted(f"agent-{i}" for i in range(10))


async def test_agents_states_account_for_their_lifetimes():
    session = await thread_session()
    runtime = ToolRuntime(session)
    model = Slow(
        script=calls_then_answer(
            ("simulate", {"x": 1.0}), ("simulate_and_wait", {"x": 2.0}), ("inline_note", {"text": "hi"})
        )
    )
    tools = [
        task_tool(simulate),
        task_tool(simulate, suspend=True, name="simulate_and_wait"),
        inline_note,
    ]
    lifetimes = []
    host = AgentHost(
        LoopRunner(model, tools, runtime=runtime),
        stagger=FAST,
        on_done=lambda r, out: lifetimes.append(r.t_end - r.t_start),
    )
    report = await host.run(inputs(10))
    await session.close()
    assert report.finished == 10
    seconds = host.states.agent_seconds()
    for state in (AgentState.WORKING, AgentState.MODEL_REQUEST, AgentState.TOOL_WAIT):
        assert seconds[state] > 0, state
    assert abs(sum(host.states.by_category().values()) / sum(lifetimes) - 1) < 0.01


async def test_the_host_records_the_loops_spans_and_suspensions(span_exporter, log_exporter):
    session = await thread_session()
    runtime = ToolRuntime(session)
    model = Scripted(script=calls_then_answer(("simulate", {"x": 1.0}), ("inline_note", {"text": "hi"})))
    runner = LoopRunner(model, [task_tool(simulate, suspend=True), inline_note], runtime=runtime)
    report = await AgentHost(runner, stagger=FAST).run(inputs(3))
    await session.close()
    assert report.finished == 3
    spans = span_exporter.get_finished_spans()
    agents = [s for s in spans if s.name == "invoke_agent"]
    tools = [s for s in spans if s.name == "execute_tool simulate"]
    assert len(agents) == 3 and len(tools) == 3
    by_id = {s.context.span_id: s for s in agents}
    assert all(t.parent.span_id in by_id for t in tools)  # each task under its agent
    assert all(t.attributes["gen_ai.tool.call.id"] == "call-0" for t in tools)
    suspends = [r for r in log_exporter.get_finished_logs() if r.log_record.event_name == "flowgentic.agent.suspend"]
    assert len(suspends) == 3


# --- construction ------------------------------------------------------------------


def test_a_task_or_mcp_tool_needs_a_runtime():
    model = Scripted(script=lambda m: AIMessage("hi"))
    with pytest.raises(ValueError, match="runtime"):
        LoopRunner(model, [task_tool(simulate)])
    with pytest.raises(ValueError, match="runtime"):
        LoopRunner(model, [mcp_tool("lookup", description="Look up.", args_schema=LookupArgs)])


def test_tool_names_must_be_unique_and_tools_must_be_tools():
    model = Scripted(script=lambda m: AIMessage("hi"))
    with pytest.raises(ValueError, match="inline_note"):
        LoopRunner(model, [inline_note, inline_note])
    with pytest.raises(ValueError, match="Verdict"):
        LoopRunner(model, [task_tool(simulate, name="Verdict")], runtime=ToolRuntime(), response_format=Verdict)
    with pytest.raises(TypeError, match="not a tool"):
        LoopRunner(model, [simulate])


def test_task_and_mcp_tools_are_bound_with_their_schemas():
    model = Binding(script=lambda m: AIMessage("hi"), bound=[])
    lookup = mcp_tool("lookup", description="Look up.", args_schema=LookupArgs)
    LoopRunner(model, [task_tool(simulate), lookup], runtime=ToolRuntime())
    schemas = {t.name: t.tool_call_schema.model_json_schema() for t in model.bound}
    assert set(schemas) == {"simulate", "lookup"}
    assert set(schemas["simulate"]["properties"]) == {"x"}
    assert set(schemas["lookup"]["properties"]) == {"material"}
    assert model.bound[0].description == "Run a simulation of x."
