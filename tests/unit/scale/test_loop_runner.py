"""Flowgentic's own agent loop in an agent host, on OpenAI-format messages and
transports: suspension with its state in a key-value store, concurrent tool
calls, tool errors returned to the model, the model-call limit, structured
answers, the history-trim hook, interrupts, and the accounting and telemetry
the host's other parts use. No LangChain: they run without it installed.
LangChain's models and tools, through its adapter: ``test_loop_langchain``."""

import asyncio
import json
import time
from collections import Counter
from typing import Any

import pytest
from _basics import (
    FAST,
    RUNS,
    LookupArgs,
    ScriptedTransport,
    broken,
    collect,
    dict_calls_then_answer,
    inputs,
    reply,
    simulate,
    thread_session,
    tool_results,
    transport,
)
from pydantic import BaseModel, ConfigDict, Field

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


async def note(text: str) -> str:
    """Take a note."""
    RUNS["note"] = RUNS.get("note", 0) + 1
    return f"noted {text}"


RUNNING = {"now": 0, "most": 0}


async def wait_a_bit(name: str) -> str:
    """Wait for 0.2 s."""
    RUNNING["now"] += 1
    RUNNING["most"] = max(RUNNING["most"], RUNNING["now"])
    try:
        await asyncio.sleep(0.2)
    finally:
        RUNNING["now"] -= 1
    return name


async def wait_long(name: str) -> str:
    """Wait for 10 s."""
    try:
        await asyncio.sleep(10)
    except asyncio.CancelledError:
        RUNS["wait_long cancelled"] = RUNS.get("wait_long cancelled", 0) + 1
        raise
    return name


def fragile(x: int) -> str:
    """Fail on odd numbers."""
    if x % 2:
        raise ValueError(f"odd {x}")
    return "even"


def ask_a_human(question: str) -> str:
    """Ask a human."""
    raise Interrupt({"question": question})


def slow_simulate(x: float) -> dict:
    """A simulation that takes a while."""
    time.sleep(0.5)
    return {"y": 2 * x}


class FakeService:
    def __init__(self):
        self.calls = []

    async def call(self, uid, tool, args, *, idempotent=False):
        self.calls.append((uid, tool, args, idempotent))
        return {"material": args["material"], "score": 0.5}


class FailingService(FakeService):
    async def call(self, uid, tool, args, *, idempotent=False):
        raise ConnectionError("service gone")


class Verdict(BaseModel):
    """The agent's verdict on its candidate."""

    score: float
    keep: bool


def turns(messages) -> int:
    """The model's replies so far."""
    return sum(1 for m in messages if m["role"] == "assistant")


# --- suspension and repeat safety ---------------------------------------------------


async def test_a_suspended_agent_resumes_from_the_store_and_nothing_runs_twice():
    session = await thread_session()
    runtime = ToolRuntime(session)
    store = CountingStore()
    model = transport(("simulate", {"x": 1.5}), ("note", {"text": "hi"}))
    runner = LoopRunner(model, [task_tool(simulate, suspend=True), note], runtime=runtime, store=store)
    outputs = {}
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(20))
    await session.close()

    assert report.finished == 20
    assert all(a.suspensions == 1 for a in report.agents)
    assert RUNS == {"simulate": 20, "note": 20}  # each ran once per agent
    assert runtime.stats()["repeats"] == 20  # the resume found the task
    # One put at the suspension, one get at the resume, one delete at the end.
    assert store.calls == {"put": 20, "get": 20, "delete": 20}
    assert store.keys() == []
    out = outputs["agent-0"]
    assert isinstance(out, LoopOutput)
    assert out.model_calls == 2 and out.structured_response is None
    assert all(isinstance(m, dict) for m in out.messages)
    assert [m["role"] for m in out.messages] == ["user", "assistant", "tool", "tool", "assistant"]
    final = out.messages[-1]["content"]
    assert '{"y": 3.0}' in final and "noted hi" in final
    # The note answered first, the task after the resume; the history keeps
    # the order of the calls.
    assert [m["tool_call_id"] for m in tool_results(out.messages)] == ["call-0", "call-1"]
    saved = store.last_put["messages"]
    assert [m["role"] for m in saved] == ["user", "assistant", "tool"]  # the note's result
    stats = runner.stats()
    assert stats["model_calls"] == 40 and stats["suspensions"] == 20
    assert stats["tool_calls"] == 40 and stats["tool_errors"] == 0
    assert (stats["store_puts"], stats["store_gets"], stats["store_deletes"]) == (20, 20, 20)


async def test_an_agent_that_never_suspends_never_touches_the_store():
    store = CountingStore()
    report = await AgentHost(
        LoopRunner(transport(("note", {"text": "hi"})), [note], store=store), stagger=FAST
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
            return reply(calls=[("call-0", "simulate", {"x": float(turns(messages) + 1)})])
        return reply(" | ".join(m["content"] for m in tool_results(messages)))

    outputs = {}
    runner = LoopRunner(ScriptedTransport(script), [task_tool(simulate, suspend=True)], runtime=runtime)
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(3))
    await session.close()
    assert report.finished == 3
    assert outputs["agent-0"].messages[-1]["content"] == '{"y": 2.0} | {"y": 4.0}'
    assert RUNS == {"simulate": 6}
    assert runtime.stats()["submitted"] == 6


async def test_a_task_tool_that_does_not_suspend_is_awaited_in_the_loop():
    session = await thread_session()
    store = CountingStore()
    outputs = {}
    report = await AgentHost(
        LoopRunner(transport(("simulate", {"x": 2.0})), [task_tool(simulate)], runtime=ToolRuntime(session), store=store),
        stagger=FAST,
        on_done=collect(outputs),
    ).run(inputs(4))
    await session.close()
    assert report.finished == 4
    assert all(a.suspensions == 0 for a in report.agents)
    assert outputs["agent-0"].messages[-1]["content"] == '{"y": 4.0}'
    assert store.calls == {}


async def test_mcp_tools_go_through_the_service_keyed_by_agent_and_turn():
    service = FakeService()
    lookup = mcp_tool("lookup", description="Look a material up.", args_schema=LookupArgs, idempotent=True)
    outputs = {}
    runner = LoopRunner(transport(("lookup", {"material": "x"})), [lookup], runtime=ToolRuntime(service=service))
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(1))
    assert report.finished == 1
    assert service.calls == [("agent-0/1/call-0", "lookup", {"material": "x"}, True)]
    assert '"score": 0.5' in outputs["agent-0"].messages[-1]["content"]


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
    runner = LoopRunner(transport(call), [tool_], runtime=ToolRuntime())
    report = await AgentHost(runner, stagger=FAST).run(inputs(1))
    (agent,) = report.agents
    assert agent.status == "failed" and agent.error.startswith("RuntimeError: ")
    assert expected in agent.error


# --- concurrency ----------------------------------------------------------------


async def test_the_tool_calls_of_one_reply_run_at_once():
    model = transport(("wait_a_bit", {"name": "a"}), ("wait_a_bit", {"name": "b"}), ("wait_a_bit", {"name": "c"}))
    outputs = {}
    RUNNING["most"] = 0
    report = await AgentHost(LoopRunner(model, [wait_a_bit]), stagger=FAST, on_done=collect(outputs)).run(inputs(1))
    assert RUNNING["most"] == 3  # the three calls ran at once
    assert report.finished == 1
    assert outputs["agent-0"].messages[-1]["content"] == "a | b | c"


# --- errors -----------------------------------------------------------------------


async def test_tool_errors_reach_the_model_and_the_agent_goes_on():
    session = await thread_session()
    seen = []

    def script(messages):
        results = tool_results(messages)
        if results:
            seen.extend(results)
            return reply("done")
        return reply(
            calls=[
                ("a", "fragile", {"x": 1}),  # raises
                ("b", "fragile", {"x": "one"}),  # invalid arguments
                ("c", "nowhere", {}),  # no such tool
                ("d", "broken", {"x": 1.0}),  # the task fails
                ("e", "simulate", {}),  # a task's argument missing
                ("f", "fragile", '{"x": '),  # arguments that did not parse
            ]
        )

    runner = LoopRunner(
        ScriptedTransport(script), [fragile, task_tool(broken), task_tool(simulate)], runtime=ToolRuntime(session)
    )
    report = await AgentHost(runner, stagger=FAST).run(inputs(1))
    await session.close()

    assert report.finished == 1
    by_id = {m["tool_call_id"]: m["content"] for m in seen}
    assert [m["tool_call_id"] for m in seen] == list("abcdef")
    assert by_id["a"] == "Error: ValueError('odd 1')\n Please fix your mistakes."
    assert "ValidationError" in by_id["b"] and "x" in by_id["b"]
    assert by_id["c"].startswith("Error: nowhere is not a valid tool, try one of [")
    assert by_id["d"] == "broken failed: ValueError: no convergence at 1.0"
    assert "ValidationError" in by_id["e"] and "simulate" not in RUNS  # never submitted
    assert "not valid JSON" in by_id["f"] and '{"x": ' in by_id["f"]
    assert runner.stats()["tool_errors"] == 6


async def test_a_task_that_cannot_be_submitted_reaches_the_model():
    """A ``prepare`` or a ``suspend`` that raises is the call's error, which
    the model reads, as a tool that raises; the reply's other calls go on.
    A runtime set up wrong (``RuntimeError``) still fails the agent."""
    session = await thread_session()

    def no_workdir(agent_id, call_id, args):
        raise KeyError("workdir")

    def no_queue(agent_id, args):
        raise ValueError("no queue")

    seen = []

    def script(messages):
        results = tool_results(messages)
        if results:
            seen.extend(results)
            return reply("done")
        return reply(
            calls=[
                ("a", "prepared", {"x": 1.0}),
                ("b", "queued", {"x": 2.0}),
                ("c", "wait_a_bit", {"name": "c"}),
            ]
        )

    runner = LoopRunner(
        ScriptedTransport(script),
        [
            task_tool(simulate, name="prepared", prepare=no_workdir),
            task_tool(simulate, name="queued", suspend=no_queue),
            wait_a_bit,
        ],
        runtime=ToolRuntime(session),
    )
    report = await AgentHost(runner, stagger=FAST).run(inputs(1))
    await session.close()
    assert report.finished == 1
    by_id = {m["tool_call_id"]: m["content"] for m in seen}
    assert by_id == {
        "a": "Error: KeyError('workdir')\n Please fix your mistakes.",
        "b": "Error: ValueError('no queue')\n Please fix your mistakes.",
        "c": "c",  # not cancelled
    }
    assert "simulate" not in RUNS  # neither task was submitted
    assert runner.stats()["tool_errors"] == 2


async def test_an_agent_that_never_answers_fails_at_its_model_call_limit():
    """The count of model calls is saved with the agent's state, so the limit
    holds across suspensions."""
    session = await thread_session()
    store = CountingStore()

    def script(messages):
        return reply(calls=[(f"c{turns(messages)}", "simulate", {"x": 1.0})])

    runner = LoopRunner(
        ScriptedTransport(script),
        [task_tool(simulate, suspend=True)],
        runtime=ToolRuntime(session),
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


def test_a_model_call_limit_below_one_is_refused():
    with pytest.raises(ValueError, match="max_model_calls"):
        LoopRunner(transport(), max_model_calls=0)


def test_the_limit_error_is_a_runtime_error():
    assert issubclass(ModelCallLimitExceeded, RuntimeError)


async def test_an_agent_that_fails_after_a_suspension_leaves_no_state():
    session = await thread_session()
    store = CountingStore()

    def script(messages):
        if turns(messages) == 0:
            return reply(calls=[("c", "simulate", {"x": 1.0})])
        raise ConnectionError("model server gone")

    runner = LoopRunner(ScriptedTransport(script), [task_tool(simulate, suspend=True)], runtime=ToolRuntime(session), store=store)
    report = await AgentHost(runner, stagger=FAST).run(inputs(3))
    await session.close()
    assert report.errors == {"ConnectionError": 3}
    assert store.calls == {"put": 3, "get": 3, "delete": 3}
    assert store.keys() == []


async def test_cancelling_the_host_while_agents_wait_leaves_the_store_empty(monkeypatch):
    session = await thread_session(workers=4)
    store = CountingStore()
    forgotten = []
    monkeypatch.setattr(Fleet, "agent_done", lambda self, agent: forgotten.append(agent))
    fleet = Fleet([transport(("slow_simulate", {"x": 1.0}))])
    runner = LoopRunner(fleet, [task_tool(slow_simulate, suspend=True)], runtime=ToolRuntime(session), store=store)
    run = asyncio.create_task(AgentHost(runner, stagger=FAST).run(inputs(4)))
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
            return reply(calls=[("v1", "Verdict", {"score": "high"})])
        if n == 1:  # an answer beside another call
            return reply(calls=[("v2", "Verdict", {"score": 0.9, "keep": True}), ("n1", "note", {"text": "x"})])
        if n == 2:  # an answer in prose
            return reply("It looks good to me.")
        return reply(calls=[("v3", "Verdict", {"score": 0.9, "keep": True})])

    model = ScriptedTransport(script)
    outputs = {}
    runner = LoopRunner(model, [note], response_format=Verdict)
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(1))

    assert report.finished == 1
    out = outputs["agent-0"]
    assert out.structured_response == Verdict(score=0.9, keep=True)
    assert out.model_calls == 4
    names = [t["function"]["name"] for t in model.calls[0][1]]
    assert names == ["note", "Verdict"]
    verdict = model.calls[0][1][1]["function"]
    assert verdict["description"] == "The agent's verdict on its candidate."
    assert set(verdict["parameters"]["properties"]) == {"score", "keep"}
    results = {m["tool_call_id"]: m["content"] for m in tool_results(out.messages)}
    assert results["v1"].startswith("Error: ValidationError") and "score" in results["v1"]
    assert "on its own" in results["v2"]
    assert results["n1"] == "noted x"  # the other call ran
    nudge = [m for m in out.messages if m["role"] == "user"][-1]
    assert "Verdict" in nudge["content"]  # asked to answer through the tool
    assert out.messages[-1] == {"role": "tool", "tool_call_id": "v3", "content": "Answer recorded."}
    assert RUNS == {"note": 1}


async def test_a_structured_answer_given_as_json_text_is_accepted():
    model = ScriptedTransport(lambda m: reply(json.dumps({"score": 0.5, "keep": False})))
    outputs = {}
    report = await AgentHost(LoopRunner(model, response_format=Verdict), stagger=FAST, on_done=collect(outputs)).run(inputs(1))
    assert report.finished == 1
    assert outputs["agent-0"].structured_response == Verdict(score=0.5, keep=False)
    assert outputs["agent-0"].model_calls == 1


# --- the history ----------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_the_trim_hook_shapes_what_the_model_sees_and_what_is_saved(kind):
    session = await thread_session()
    store = CountingStore()
    seen = []

    def script(messages):
        seen.append([m["role"] for m in messages])
        if len(seen) < 4:  # three turns of tools, then the answer
            return reply(calls=[(f"c{len(seen)}", "simulate", {"x": 1.0})])
        return reply("done")

    def keep_last_turn(messages):
        return messages[:1] + messages[-2:] if len(messages) > 3 else messages

    async def keep_last_turn_async(messages):
        return keep_last_turn(messages)

    runner = LoopRunner(
        ScriptedTransport(script),
        [task_tool(simulate, suspend=True)],
        runtime=ToolRuntime(session),
        store=store,
        system_prompt="Be brief.",
        trim=keep_last_turn if kind == "sync" else keep_last_turn_async,
        max_model_calls=10,
    )
    outputs = {}
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(1))
    await session.close()

    assert report.finished == 1
    # The system prompt goes with every call, and is never trimmed or saved.
    assert seen == [
        ["system", "user"],
        ["system", "user", "assistant", "tool"],
        ["system", "user", "assistant", "tool"],
        ["system", "user", "assistant", "tool"],
    ]
    assert [m["role"] for m in store.last_put["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert [m["role"] for m in outputs["agent-0"].messages] == ["user", "assistant", "tool", "assistant"]
    assert outputs["agent-0"].model_calls == 4  # trimming keeps the count


async def test_inputs_in_every_form_and_the_callers_list_unchanged():
    given = [{"role": "user", "content": "go"}]
    agents = [
        Agent("a", "go"),
        Agent("b", given),
        Agent("c", {"messages": [("human", "go")]}),
        Agent("d", {"role": "user", "content": "go"}),
    ]
    model = ScriptedTransport(lambda m: reply(f"{len(m)} {m[-1]['content']}"))
    outputs = {}
    report = await AgentHost(
        LoopRunner(model, system_prompt="Be brief."), stagger=FAST, on_done=collect(outputs)
    ).run(agents)
    assert report.finished == 4
    for out in outputs.values():
        assert out.messages == [{"role": "user", "content": "go"}, {"role": "assistant", "content": "2 go"}]
    assert given == [{"role": "user", "content": "go"}]


async def test_an_input_that_is_not_messages_fails_its_agent_at_start():
    model = ScriptedTransport(lambda m: reply("done"))
    agents = [
        Agent("a", {"task": "go"}),  # neither messages nor a role
        Agent("b", [{"content": "go"}]),
        Agent("c", {"messages": [{"text": "go"}]}),
    ]
    report = await AgentHost(LoopRunner(model), stagger=FAST).run(agents)
    assert [a.status for a in report.agents] == ["failed"] * 3
    assert all(a.error.startswith("TypeError: not a message: ") for a in report.agents)
    assert model.calls == []


class MoveArgs(BaseModel):
    """Arguments the model gives under their aliases."""

    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str = "here"


async def test_arguments_given_under_their_alias_reach_the_tool():
    got = []

    class Move:
        name = "move"
        description = "Move a sample."
        args_schema = MoveArgs

        async def __call__(self, from_: str, to: str = "there") -> str:
            got.append((from_, to))
            return f"{from_} -> {to}"

    outputs = {}
    model = transport(("move", {"from": "shelf"}), ("move", {"from": "bench", "to": "oven"}))
    runner = LoopRunner(model, [Move()])
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(1))
    assert report.finished == 1
    assert got == [("shelf", "there"), ("bench", "oven")]  # a default not given stays the tool's
    (schema,) = runner.tool_schemas
    assert list(schema["function"]["parameters"]["properties"]) == ["from", "to"]


# --- interrupts -------------------------------------------------------------------


async def test_a_tool_that_interrupts_ends_its_agent_as_interrupted():
    store = CountingStore()
    outputs = {}
    runner = LoopRunner(transport(("ask_a_human", {"question": "go on?"}), ("note", {"text": "hi"})), [ask_a_human, note], store=store)
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(2))

    assert [a.status for a in report.agents] == ["interrupted", "interrupted"]
    (stop,) = report.agents[0].interrupts
    assert isinstance(stop, Interruption)
    assert stop.value == {"question": "go on?"}
    assert stop.tool_call["id"] == "call-0" and stop.tool_call["function"]["name"] == "ask_a_human"
    # The note answered; the question did not.
    assert [m["role"] for m in stop.messages] == ["user", "assistant", "tool"]
    assert stop.messages[-1]["tool_call_id"] == "call-1"
    assert stop.model_calls == 1
    assert outputs == {"agent-0": None, "agent-1": None}
    assert RUNS == {"note": 2}
    assert store.calls == {} and store.keys() == []
    assert runner.stats()["interrupts"] == 2


# --- models, accounting, telemetry -------------------------------------------------


async def test_a_fleet_takes_the_agents_calls_and_hears_when_they_end(monkeypatch):
    forgotten = []
    monkeypatch.setattr(Fleet, "agent_done", lambda self, agent: forgotten.append(agent))
    replicas = [transport(("note", {"text": "hi"})), transport(("note", {"text": "hi"}))]
    fleet = Fleet(replicas)
    runner = LoopRunner(fleet, [note], model_kwargs={"max_tokens": 64})
    report = await AgentHost(runner, stagger=FAST).run(inputs(10))
    assert report.finished == 10
    assert fleet.stats()["calls"] == 20
    assert sorted(forgotten) == sorted(f"agent-{i}" for i in range(10))
    calls = replicas[0].calls + replicas[1].calls
    assert all(kwargs == {"max_tokens": 64} for _, _, kwargs in calls)
    assert all(tools == runner.tool_schemas for _, tools, _ in calls)  # bound once


async def test_agents_states_account_for_their_lifetimes():
    session = await thread_session()
    model = transport(
        ("simulate", {"x": 1.0}), ("simulate_and_wait", {"x": 2.0}), ("note", {"text": "hi"}), latency_s=0.01
    )
    tools = [task_tool(simulate), task_tool(simulate, suspend=True, name="simulate_and_wait"), note]
    lifetimes = []
    host = AgentHost(
        LoopRunner(model, tools, runtime=ToolRuntime(session)),
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
    model = ScriptedTransport(
        dict_calls_then_answer(("simulate", {"x": 1.0}), ("note", {"text": "hi"})),
        model="qwen3-8b",
        base_url="http://node-1:8000/v1",
    )
    runner = LoopRunner(model, [task_tool(simulate, suspend=True), note], runtime=ToolRuntime(session))
    report = await AgentHost(runner, stagger=FAST).run(inputs(3))
    await session.close()
    assert report.finished == 3
    spans = span_exporter.get_finished_spans()
    agents = {s.context.span_id: s for s in spans if s.name == "invoke_agent"}
    tools = [s for s in spans if s.name == "execute_tool simulate"]
    chats = [s for s in spans if s.name == "chat qwen3-8b"]
    assert len(agents) == 3 and len(tools) == 3 and len(chats) == 6
    assert all(s.parent.span_id in agents for s in tools + chats)  # each under its agent
    assert all(t.attributes["gen_ai.tool.call.id"] == "call-0" for t in tools)
    assert all(c.attributes["gen_ai.usage.input_tokens"] == 10 for c in chats)
    assert {c.attributes["server.address"] for c in chats} == {"node-1"}
    suspends = [r for r in log_exporter.get_finished_logs() if r.log_record.event_name == "flowgentic.agent.suspend"]
    assert len(suspends) == 3


# --- construction ------------------------------------------------------------------


def test_a_task_or_mcp_tool_needs_a_runtime():
    with pytest.raises(ValueError, match="runtime"):
        LoopRunner(transport(), [task_tool(simulate)])
    with pytest.raises(ValueError, match="runtime"):
        LoopRunner(transport(), [mcp_tool("lookup", description="Look up.", args_schema=LookupArgs)])


def test_tool_names_must_be_unique_and_tools_must_be_tools():
    with pytest.raises(ValueError, match="note"):
        LoopRunner(transport(), [note, note])
    with pytest.raises(ValueError, match="Verdict"):
        LoopRunner(transport(), [task_tool(simulate, name="Verdict")], runtime=ToolRuntime(), response_format=Verdict)
    with pytest.raises(TypeError, match="not a tool"):
        LoopRunner(transport(), [42])
    with pytest.raises(TypeError, match="Transport"):
        LoopRunner(object())


def test_the_tools_json_schemas_come_from_pydantic():
    class Weighted:
        name = "weigh"
        description = "Weigh a sample."
        args_schema = LookupArgs

        async def __call__(self, material: str) -> str:
            return material

    lookup = mcp_tool("lookup", description="Look up.", args_schema=LookupArgs)
    runner = LoopRunner(transport(), [task_tool(simulate), lookup, note, Weighted()], runtime=ToolRuntime())
    by_name = {t["function"]["name"]: t["function"] for t in runner.tool_schemas}
    assert list(by_name) == ["simulate", "lookup", "note", "weigh"]
    assert by_name["simulate"]["description"] == "Run a simulation of x."
    assert by_name["simulate"]["parameters"] == {
        "type": "object",
        "properties": {"x": {"type": "number"}},
        "required": ["x"],
    }
    assert by_name["lookup"]["parameters"]["properties"] == {"material": {"type": "string"}}
    assert by_name["note"]["description"] == "Take a note."
    assert by_name["note"]["parameters"]["properties"] == {"text": {"type": "string"}}
    assert by_name["weigh"]["description"] == "Weigh a sample."


# --- more failures ------------------------------------------------------------------


async def test_a_failed_mcp_call_or_task_output_reaches_the_model():
    session = await thread_session()
    lookup = mcp_tool("lookup", description="Look up.", args_schema=LookupArgs)

    def unreadable(value):
        raise KeyError("y")

    seen = []

    def script(messages):
        results = tool_results(messages)
        if results:
            seen.extend(results)
            return reply("done")
        return reply(calls=[("a", "lookup", {"material": "x"}), ("b", "lookup", {}), ("c", "simulate", {"x": 1.0})])

    runner = LoopRunner(
        ScriptedTransport(script),
        [lookup, task_tool(simulate, output=unreadable)],
        runtime=ToolRuntime(session, FailingService()),
    )
    report = await AgentHost(runner, stagger=FAST).run(inputs(1))
    await session.close()
    assert report.finished == 1
    by_id = {m["tool_call_id"]: m["content"] for m in seen}
    assert by_id["a"] == "lookup failed: ConnectionError: service gone"
    assert "ValidationError" in by_id["b"] and "material" in by_id["b"]
    assert by_id["c"] == "Error: KeyError('y')\n Please fix your mistakes."


async def test_a_call_that_fails_the_agent_stops_the_others_of_its_reply():
    runner = LoopRunner(transport(("wait_long", {"name": "a"}), ("simulate", {"x": 1.0})), [wait_long, task_tool(simulate)], runtime=ToolRuntime())
    t = time.perf_counter()
    report = await AgentHost(runner, stagger=FAST).run(inputs(1))
    assert time.perf_counter() - t < 5  # did not wait for the 10 s call
    assert report.errors == {"RuntimeError": 1}
    assert RUNS == {"wait_long cancelled": 1}


class UndeletableStore(CountingStore):
    def delete(self, key):
        raise ConnectionError("store gone")


async def test_a_state_that_cannot_be_deleted_leaves_the_agent_finished(caplog):
    session = await thread_session()
    runner = LoopRunner(
        transport(("simulate", {"x": 1.0})), [task_tool(simulate, suspend=True)], runtime=ToolRuntime(session), store=UndeletableStore()
    )
    report = await AgentHost(runner, stagger=FAST).run(inputs(3))
    await session.close()
    assert report.finished == 3
    assert runner.stats()["delete_failures"] == 3
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "stays in the store" in r.message]
    assert len(warnings) == 1  # once; the others at debug level


class ForgetfulStore(CountingStore):
    def put(self, key, value):
        self.calls["put"] += 1  # and keeps nothing


async def test_an_agent_whose_state_is_lost_fails_on_resume():
    session = await thread_session()
    runner = LoopRunner(
        transport(("simulate", {"x": 1.0})), [task_tool(simulate, suspend=True)], runtime=ToolRuntime(session), store=ForgetfulStore()
    )
    report = await AgentHost(runner, stagger=FAST).run(inputs(1))
    await session.close()
    (agent,) = report.agents
    assert agent.status == "failed"
    assert agent.error == "RuntimeError: agent 'agent-0' has no saved state under 'loop/agent-0'"
