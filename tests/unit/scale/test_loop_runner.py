"""Flowgentic's own agent loop in an agent host, on OpenAI-format messages and
transports: suspension with its state in a key-value store, concurrent tool
calls, tool errors returned to the model, the model-call limit, structured
answers, the history-trim hook, interrupts, and the accounting and telemetry
the host's other parts use. No LangChain: they run without it installed.
LangChain's models and tools, through its adapter: ``test_loop_langchain``."""

import asyncio
import json
import re
import threading
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
from opentelemetry.trace import StatusCode
from pydantic import BaseModel, ConfigDict, Field

from flowgentic.fleet import Fleet
from flowgentic.host import Agent, AgentHost, AgentState, AgentStates, Finished, Suspended
from flowgentic.loop import (
    Interrupt,
    Interruption,
    LoopOutput,
    LoopRunner,
    ModelCallLimitExceeded,
)
from flowgentic.store import MemoryStore
from flowgentic import _agents
from flowgentic.tools import ToolRuntime, ToolSetupError, mcp_tool, task_tool

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
    ((key, *call),) = service.calls  # agent/turn/call, the turn with the runner's own token
    assert re.fullmatch(r"agent-0/[0-9a-f]{8}-1/call-0", key)
    assert call == ["lookup", {"material": "x"}, True]
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
    assert agent.status == "failed" and agent.error.startswith("ToolSetupError: ")
    assert expected in agent.error
    assert issubclass(ToolSetupError, RuntimeError)


class RefusingService(FakeService):
    async def call(self, uid, tool, args, *, idempotent=False):
        raise RuntimeError("the server refused the call")


async def test_a_tools_own_runtime_error_reaches_the_model():
    """Only a runtime set up wrong (``ToolSetupError``) fails the agent; a
    ``RuntimeError`` of the tool's own, such as ``NotImplementedError`` from a
    ``suspend``, a ``prepare`` out of slots or an MCP server's refusal, is the
    call's error, which the model reads."""
    session = await thread_session()

    def no_slot(agent_id, call_id, args):
        raise RuntimeError("no slot left")

    def not_yet(agent_id, args):
        raise NotImplementedError("no policy for this")

    seen = []

    def script(messages):
        results = tool_results(messages)
        if results:
            seen.extend(results)
            return reply("done")
        return reply(calls=[("a", "prepared", {"x": 1.0}), ("b", "queued", {"x": 2.0}), ("c", "lookup", {"material": "x"})])

    lookup = mcp_tool("lookup", description="Look up.", args_schema=LookupArgs)
    runner = LoopRunner(
        ScriptedTransport(script),
        [task_tool(simulate, name="prepared", prepare=no_slot), task_tool(simulate, name="queued", suspend=not_yet), lookup],
        runtime=ToolRuntime(session, RefusingService()),
    )
    report = await AgentHost(runner, stagger=FAST).run(inputs(1))
    await session.close()
    assert report.finished == 1
    by_id = {m["tool_call_id"]: m["content"] for m in seen}
    assert by_id["a"] == "Error: RuntimeError('no slot left')\n Please fix your mistakes."
    assert by_id["b"] == "Error: NotImplementedError('no policy for this')\n Please fix your mistakes."
    assert by_id["c"] == "lookup failed: RuntimeError: the server refused the call"


# --- inputs ------------------------------------------------------------------------


async def test_a_role_and_content_tuple_is_one_message():
    """As LangGraph reads it."""
    model = ScriptedTransport(lambda messages: reply("ok"))
    runner = LoopRunner(model)
    await AgentHost(runner, stagger=FAST).run(
        [Agent("a", ("user", "hello")), Agent("b", [("system", "be brief"), ("human", "hi")])]
    )
    sent = sorted((m for m, _, _ in model.calls), key=len)
    assert sent[0] == [{"role": "user", "content": "hello"}]
    assert sent[1] == [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]


async def test_calls_in_an_input_history_that_the_conversation_moved_past_do_not_run():
    """A history whose assistant message has calls with no results, followed
    by a user's message: those calls are history, not pending work; only
    calls followed by nothing but tool results are, as when an agent
    resumes."""
    ran = []

    def act(x: int) -> str:
        """Act."""
        ran.append(x)
        return "did"

    model = ScriptedTransport(lambda messages: reply("ok"))
    history = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "old", "type": "function", "function": {"name": "act", "arguments": '{"x": 9}'}}],
        },
        {"role": "user", "content": "a new question"},
    ]
    report = await AgentHost(LoopRunner(model, [act]), stagger=FAST).run([Agent("a", {"messages": history})])
    assert report.finished == 1 and ran == []
    (sent, _, _), *_ = model.calls
    # each such call gets a result that says it was not run, before the user's
    # message, as a server requires a result for every call
    assert sent[:2] == history[:2] and sent[3:] == history[2:]
    assert sent[2]["role"] == "tool" and sent[2]["tool_call_id"] == "old"
    assert sent[2]["content"].startswith("Error: ") and "not run" in sent[2]["content"]
    # with nothing but a result after it, the other call is pending, and runs first
    pending = [*history[:2], {"role": "tool", "tool_call_id": "other", "content": "x"}]
    pending[1] = {
        **pending[1],
        "tool_calls": [
            {"id": "other", "type": "function", "function": {"name": "act", "arguments": '{"x": 1}'}},
            pending[1]["tool_calls"][0],
        ],
    }
    await AgentHost(LoopRunner(model, [act]), stagger=FAST).run([Agent("b", {"messages": pending})])
    assert ran == [9]


async def test_text_reaches_the_model_as_written():
    """A tool's result and the reply's arguments keep non-ASCII text, as
    LangChain's ToolNode and langchain-openai send them."""

    def city(name: str) -> dict:
        """A city's note."""
        return {"name": name, "note": "Zürich ✓"}

    model = transport(("city", {"name": "Zürich"}))
    await AgentHost(LoopRunner(model, [city]), stagger=FAST).run(inputs(1))
    _, (second, _, _) = model.calls
    assert second[1]["tool_calls"][0]["function"]["arguments"] == '{"name": "Zürich"}'
    assert second[2]["content"] == '{"name": "Zürich", "note": "Zürich ✓"}'


async def test_a_sync_inline_tool_runs_off_the_event_loop():
    """In a thread, as ToolNode runs one, so that a slow tool does not stall
    every agent; with the agent's context, so its accounting and spans hold.
    On the event loop, four agents' calls would run one after another; in
    threads, they overlap."""
    seen, spans = {}, []

    def slow(x: int) -> int:
        """Slow."""
        t0 = time.monotonic()
        seen[threading.get_ident()] = _agents.current_agent()
        time.sleep(0.2)
        spans.append((t0, time.monotonic()))
        return x

    async def fast(x: int) -> int:
        """Fast."""
        seen["fast"] = threading.get_ident()
        return x

    loop_thread = threading.get_ident()
    model = ScriptedTransport(dict_calls_then_answer(("slow", {"x": 1}), ("fast", {"x": 1})))
    runner = LoopRunner(model, [slow, fast])
    report = await AgentHost(runner, stagger=FAST).run(inputs(4))
    assert report.finished == 4
    assert seen.pop("fast") == loop_thread  # an async tool stays on the loop
    assert loop_thread not in seen and all(agent is not None for agent in seen.values())
    assert len(spans) == 4
    starts, ends = sorted(s for s, _ in spans), sorted(e for _, e in spans)
    assert starts[1] < ends[0]  # at least two ran at once
    runner.close()


async def test_sync_inline_tools_run_on_the_runners_own_threads():
    """Not on the event loop's default executor, which also resolves host
    names for the model's connections: slow tools queued there would hold a
    model call's connect. ``close()`` ends the threads; a later run makes
    new ones."""
    names = []

    def where(x: int) -> int:
        """Where."""
        names.append(threading.current_thread().name)
        return x

    model = ScriptedTransport(dict_calls_then_answer(("where", {"x": 1})))
    runner = LoopRunner(model, [where])
    await AgentHost(runner, stagger=FAST).run(inputs(3))
    assert len(names) == 3 and all(n.startswith("flowgentic-inline") for n in names)
    runner.close()
    runner.close()  # again: nothing
    await AgentHost(runner, stagger=FAST).run(inputs(1, "again"))
    assert len(names) == 4
    runner.close()


async def test_text_that_utf8_cannot_carry_does_not_stop_the_conversation():
    """A lone surrogate, as in a file name Linux could not decode, goes to the
    model as its escape, so neither this call nor any after it fails."""
    name = b"run-\xff.dat".decode("utf-8", "surrogateescape")

    def files() -> dict:
        """List files."""
        return {"files": [name]}

    def first() -> str:
        """The first file."""
        return name

    model = transport(("files", {}), ("first", {}))
    report = await AgentHost(LoopRunner(model, [files, first]), stagger=FAST).run(inputs(1))
    assert report.finished == 1
    _, (second, _, _) = model.calls
    json.dumps(second, ensure_ascii=False).encode("utf-8")  # sendable
    results = [m["content"] for m in second if m["role"] == "tool"]
    assert results == ['{"files": ["run-\\udcff.dat"]}', "run-\\udcff.dat"]


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
    # an inline tool's call is an execute_tool span too, as a task's and an MCP tool's are
    notes = [s for s in spans if s.name == "execute_tool note"]
    assert len(notes) == 3 and all(s.parent.span_id in agents for s in notes)
    for span in notes:
        assert span.attributes["gen_ai.operation.name"] == "execute_tool"
        assert span.attributes["gen_ai.tool.name"] == "note"
        assert span.attributes["gen_ai.tool.call.id"] == "call-1"
        assert span.attributes["flowgentic.tool.kind"] == "inline"
        assert span.attributes["flowgentic.tool.outcome"] == "ok"
    assert all(c.attributes["gen_ai.usage.input_tokens"] == 10 for c in chats)
    assert {c.attributes["server.address"] for c in chats} == {"node-1"}
    suspends = [r for r in log_exporter.get_finished_logs() if r.log_record.event_name == "flowgentic.agent.suspend"]
    assert len(suspends) == 3


async def test_an_inline_tools_span_says_how_it_ended(span_exporter):
    def fails(x: float) -> float:
        """Fail."""
        raise ValueError("no")

    def stops(x: float) -> float:
        """Stop for a person."""
        raise Interrupt({"ask": "approve?"})

    model = transport(("fails", {"x": 1.0}), ("stops", {"x": 1.0}))
    report = await AgentHost(LoopRunner(model, [fails, stops]), stagger=FAST).run(inputs(1))
    assert report.outcomes == {"interrupted": 1}
    spans = {s.name: s for s in span_exporter.get_finished_spans()}
    failed, stopped = spans["execute_tool fails"], spans["execute_tool stops"]
    assert failed.attributes["flowgentic.tool.outcome"] == "error"
    assert failed.attributes["error.type"] == "ValueError"
    assert failed.status.status_code == StatusCode.ERROR  # as a task's
    assert [e.name for e in failed.events] == ["exception"]
    assert stopped.attributes["flowgentic.tool.outcome"] == "interrupted"
    assert stopped.status.status_code != StatusCode.ERROR


# --- runners side by side ------------------------------------------------------------


def pop_a(x: float) -> dict:
    """Simulate, slowly, for population A."""
    time.sleep(0.3)
    return {"pop": "A"}


def pop_b(x: float) -> dict:
    """Simulate, quickly, for population B."""
    time.sleep(0.02)
    return {"pop": "B"}


async def pause() -> str:
    """Wait a moment."""
    await asyncio.sleep(0.05)
    return "paused"


def same_calls(messages):
    """The same call ids for every agent and population: a simulation and a
    pause, then the simulation's result as the answer."""
    results = {m["tool_call_id"]: m["content"] for m in tool_results(messages)}
    if "c1" in results:
        return reply(results["c1"])
    return reply(calls=[("c1", "simulate", {"x": 1.0}), ("c2", "pause", {})])


def bounded(runner, limit=50):
    """Fail the agent, not the test run, if the runner is resumed without end."""
    resume, count = runner.resume, [0]

    async def guarded(agent, done):
        count[0] += 1
        if count[0] > limit:
            raise AssertionError("resumed without end")
        return await resume(agent, done)

    runner.resume = guarded
    return runner


def answers(outputs):
    return {json.loads(o.messages[-1]["content"])["pop"] for o in outputs.values()}


async def test_two_hosts_whose_agents_share_ids_each_get_their_own_tasks():
    """Two populations in one process, each with its own runtime and session,
    whose agents share ids and call ids, and wait on a pause beside their
    task: each agent resumes once, on its own task."""
    sessions = [await thread_session(), await thread_session()]
    outputs, hosts = [{}, {}], []
    for fn, session, out in zip((pop_a, pop_b), sessions, outputs, strict=True):
        tools = [task_tool(fn, name="simulate", suspend=True), pause]
        runner = bounded(LoopRunner(ScriptedTransport(same_calls), tools, runtime=ToolRuntime(session)))
        hosts.append(AgentHost(runner, stagger=FAST, on_done=collect(out)))
    try:
        reports = await asyncio.wait_for(asyncio.gather(*(h.run(inputs(3)) for h in hosts)), 20)
    finally:
        for session in sessions:
            await session.close()
    assert [r.finished for r in reports] == [3, 3]
    assert [answers(out) for out in outputs] == [{"A"}, {"B"}]
    assert all(a.suspensions == 1 for r in reports for a in r.agents)


async def test_two_runners_sharing_a_runtime_do_not_share_tasks():
    """A runtime shared by two runners whose agents share ids and call ids:
    each runner's task is its own, not the other's asked for again."""
    session = await thread_session()
    runtime = ToolRuntime(session)
    outputs, hosts = [{}, {}], []
    for fn, out in zip((pop_a, pop_b), outputs, strict=True):
        tools = [task_tool(fn, name="simulate", suspend=True), pause]
        runner = bounded(LoopRunner(ScriptedTransport(same_calls), tools, runtime=runtime))
        hosts.append(AgentHost(runner, stagger=FAST, on_done=collect(out)))
    try:
        reports = await asyncio.wait_for(asyncio.gather(*(h.run(inputs(2)) for h in hosts)), 20)
    finally:
        await session.close()
    assert [r.finished for r in reports] == [2, 2]
    assert [answers(out) for out in outputs] == [{"A"}, {"B"}]
    assert runtime.stats()["submitted"] == 4


async def as_agent(agent_id, step):
    """Run ``step()`` as the host runs an agent: in a task of its own, with the
    agent current."""

    async def run():
        _agents.set_current_agent(agent_id, AgentStates([agent_id]))
        return await step()

    return await asyncio.create_task(run())


async def test_a_runner_resumes_an_agent_another_runner_suspended():
    """The turn's token is saved with the agent's state, so a runner other
    than the one that suspended it, sharing its runtime and store, finds the
    agent's task instead of submitting it again, and ends the agent."""
    session = await thread_session()
    runtime, store = ToolRuntime(session), MemoryStore()
    tool = task_tool(pop_b, name="simulate", suspend=True)

    def runner():
        model = ScriptedTransport(dict_calls_then_answer(("simulate", {"x": 1.0})))
        return LoopRunner(model, [tool], runtime=runtime, store=store)

    first, second = runner(), runner()
    agent = Agent("x", "go")
    try:
        step = await as_agent("x", lambda: first.start(agent))
        assert isinstance(step, Suspended)
        ((rid, key),) = step.waits
        await asyncio.wait_for(asyncio.shield(_agents.pending(key)), 5)
        done = await as_agent("x", lambda: second.resume(agent, {rid: {"flowgentic_done": key}}))
    finally:
        await session.close()
    assert isinstance(done, Finished) and '"pop": "B"' in done.output.messages[-1]["content"]
    assert runtime.stats()["submitted"] == 1 and runtime.stats()["repeats"] == 1
    assert store.keys() == []  # the second runner ended the agent


# --- the decision record -------------------------------------------------------------


async def test_a_task_commits_when_the_model_reply_that_made_it_reached_the_agent():
    """Each task's record commits at the reply that asked for it, not when the
    runtime got the call: through a fleet, the end of the fleet's call
    record; with a transport alone, when the loop got the reply."""
    session = await thread_session()
    calls, through_fleet, alone, answered = [], [], [], []
    fleet = Fleet([transport(("simulate", {"x": 1.5}))])
    fleet.add_listener(lambda record, reply: calls.append((record, reply)))
    runtime = ToolRuntime(session)
    runtime.add_listener(through_fleet.append)
    await AgentHost(LoopRunner(fleet, [task_tool(simulate, suspend=True)], runtime=runtime), stagger=FAST).run(
        inputs(3)
    )

    class Timed(ScriptedTransport):
        async def complete(self, messages, tools=None, **kwargs):
            out = await super().complete(messages, tools, **kwargs)
            if out.tool_calls:
                answered.append(time.time())
            return out

    own = ToolRuntime(session)
    own.add_listener(alone.append)
    model = Timed(dict_calls_then_answer(("simulate", {"x": 1.5})))
    await AgentHost(LoopRunner(model, [task_tool(simulate, suspend=True)], runtime=own), stagger=FAST).run(
        inputs(3, "solo")
    )
    await session.close()
    replies = {record.agent_id: record.t_end for record, reply in calls if reply.tool_calls}
    assert len(through_fleet) == 3 and len(replies) == 3
    for record in through_fleet:
        assert record.t_commit == replies[record.info.agent_id] < record.t_submit
    assert len(alone) == 3 and len(answered) == 3
    for record in alone:
        assert min(answered) <= record.t_commit < record.t_submit


# --- construction ------------------------------------------------------------------


async def test_a_task_tool_the_runtime_cannot_run_is_refused_when_the_runner_is_made():
    """As ``bind_tools`` does for a graph: a backend the session lacks fails
    before any agent runs, not at an agent's first call."""
    session = await thread_session()
    try:
        with pytest.raises(ValueError, match="no backend 'gpu'"):
            LoopRunner(transport(), [task_tool(simulate, backend="gpu")], runtime=ToolRuntime(session))
        with pytest.raises(ValueError, match="task_backend_specific_kwargs"):
            LoopRunner(
                transport(),
                [task_tool(simulate, task_backend_specific_kwargs="cpus=4")],
                runtime=ToolRuntime(session),
            )
    finally:
        await session.close()


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
    assert report.errors == {"ToolSetupError": 1}  # a RuntimeError
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
