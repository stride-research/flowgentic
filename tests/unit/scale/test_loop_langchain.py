"""Flowgentic's own agent loop with LangChain, through its adapter: a LangChain
chat model as the model, the fleet's chat model, LangChain tools inline,
LangChain messages as input."""

from typing import Annotated

import pytest

pytest.importorskip("langchain_core")

from _basics import FAST, LookupArgs, ScriptedTransport, collect, inputs, reply, transport  # noqa: E402
from _support import RUNS, Scripted, calls_then_answer, simulate, thread_session, tool_results  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langchain_core.tools import InjectedToolCallId, tool  # noqa: E402

from flowgentic.fleet import Fleet  # noqa: E402
from flowgentic.host import Agent, AgentHost  # noqa: E402
from flowgentic.loop import Interrupt, LoopRunner  # noqa: E402
from flowgentic.tools import ToolRuntime, mcp_tool, task_tool  # noqa: E402


@pytest.fixture(autouse=True)
def clear_runs():
    RUNS.clear()


async def test_langchain_messages_as_input():
    model = ScriptedTransport(lambda m: reply(f"{len(m)} {m[-1]['content']}"))
    outputs = {}
    report = await AgentHost(LoopRunner(model), stagger=FAST, on_done=collect(outputs)).run(
        [Agent("e", [HumanMessage("go")]), Agent("f", HumanMessage("go"))]
    )
    assert report.finished == 2
    for out in outputs.values():
        assert out.messages == [{"role": "user", "content": "go"}, {"role": "assistant", "content": "1 go"}]


@tool
def lc_note(text: str) -> str:
    """Take a note, LangChain's way."""
    RUNS["lc_note"] = RUNS.get("lc_note", 0) + 1
    return f"noted {text}"


@tool
def lc_ask(question: str) -> str:
    """Ask a human, LangChain's way."""
    raise Interrupt(question)


async def test_a_langchain_chat_model_and_tools_run_through_the_adapter():
    """A LangChain chat model as the loop's model, and LangChain tools as its
    inline tools: the loop's messages stay OpenAI-format dicts."""
    session = await thread_session()
    seen = []

    def script(messages):
        seen.append(messages)
        return calls_then_answer(("simulate", {"x": 1.5}), ("lc_note", {"text": "hi"}))(messages)

    outputs = {}
    runner = LoopRunner(Scripted(script=script), [task_tool(simulate, suspend=True), lc_note], runtime=ToolRuntime(session))
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(3))
    await session.close()
    assert report.finished == 3 and all(a.suspensions == 1 for a in report.agents)
    out = outputs["agent-0"]
    assert all(isinstance(m, dict) for m in out.messages)
    final = out.messages[-1]["content"]
    assert '{"y": 3.0}' in final and "noted hi" in final
    assert isinstance(seen[0][0], HumanMessage)  # the model got LangChain's messages
    assert RUNS == {"simulate": 3, "lc_note": 3}
    by_name = {t["function"]["name"]: t["function"] for t in runner.tool_schemas}
    assert by_name["lc_note"]["description"] == "Take a note, LangChain's way."


async def test_a_langchain_tool_can_interrupt():
    runner = LoopRunner(transport(("lc_ask", {"question": "go on?"})), [lc_ask])
    report = await AgentHost(runner, stagger=FAST).run(inputs(1))
    (agent,) = report.agents
    assert agent.status == "interrupted" and agent.interrupts[0].value == "go on?"


async def test_the_fleets_chat_model_runs_as_its_fleet():
    fleet = Fleet([Scripted(script=calls_then_answer(("lc_note", {"text": "hi"})))])
    report = await AgentHost(LoopRunner(fleet.chat_model(), [lc_note]), stagger=FAST).run(inputs(3))
    assert report.finished == 3
    assert fleet.stats()["calls"] == 6


async def test_a_reply_from_a_langchain_model_keeps_its_invalid_calls():
    def script(messages):
        if any(m.type == "tool" for m in messages):
            return AIMessage("done")
        return AIMessage(
            "",
            invalid_tool_calls=[{"name": "lc_note", "args": "{text: ", "id": "f", "error": "bad JSON", "type": "invalid_tool_call"}],
        )

    outputs = {}
    report = await AgentHost(LoopRunner(Scripted(script=script), [lc_note]), stagger=FAST, on_done=collect(outputs)).run(inputs(1))
    assert report.finished == 1
    (result,) = tool_results(outputs["agent-0"].messages)
    assert "bad JSON" in result["content"] and result["tool_call_id"] == "f"


@tool
def lc_stamp(text: str, call_id: Annotated[str, InjectedToolCallId]) -> str:
    """Stamp a text with the id of the call that asked for it."""
    return f"{call_id}: {text}"


async def test_a_langchain_tool_gets_the_id_of_its_call():
    outputs = {}
    runner = LoopRunner(transport(("lc_stamp", {"text": "hi"})), [lc_stamp])
    report = await AgentHost(runner, stagger=FAST, on_done=collect(outputs)).run(inputs(1))
    assert report.finished == 1
    (result,) = tool_results(outputs["agent-0"].messages)
    assert result == {"role": "tool", "tool_call_id": "call-0", "content": "call-0: hi"}
    (schema,) = runner.tool_schemas
    assert list(schema["function"]["parameters"]["properties"]) == ["text"]  # the model never sees it


def test_a_langchain_tool_that_needs_a_graphs_state_is_refused():
    from langgraph.prebuilt import InjectedState

    @tool
    def lc_peek(question: str, state: Annotated[dict, InjectedState]) -> str:
        """Read the graph's state."""
        return question

    with pytest.raises(TypeError, match="lc_peek.*'state'"):
        LoopRunner(transport(), [lc_peek])


def test_tools_bound_for_langgraph_are_refused_for_their_declarations():
    """``flowgentic.langgraph.bind_tools`` makes LangGraph's tools, which
    suspend through LangGraph's interrupt: the loop takes the declarations."""
    from flowgentic.langgraph import bind_tools

    lookup = mcp_tool("lookup", description="Look up.", args_schema=LookupArgs)
    for bound in bind_tools(ToolRuntime(), [task_tool(simulate), lookup]):
        with pytest.raises(TypeError, match=f"{bound.name}.*task_tool or mcp_tool declaration"):
            LoopRunner(transport(), [bound], runtime=ToolRuntime())



# --- the adapter's round trips -------------------------------------------------------


def test_a_reply_keeps_its_reasoning_through_a_langchain_message():
    from flowgentic.fleet import InvalidToolCall, Reply, ToolCall, Usage
    from flowgentic.langchain import reply_from_message, to_ai_message

    reply = Reply(
        "hi",
        (ToolCall("c1", "f", {"x": 1}),),
        (InvalidToolCall("c2", "g", "{bad", "nope"),),
        Usage(10, 5, 3, 2),
        reasoning="think first",
        finish_reason="tool_calls",
        id="r1",
        model="m",
    )
    assert reply_from_message(to_ai_message(reply)) == reply


def test_a_tool_result_keeps_the_call_it_answers():
    """An assistant message's invalid calls stay in its tool calls, with
    ``{}`` as arguments as ``Reply.message()`` keeps them, so that the tool
    messages answering them answer a call a server knows."""
    from langchain_core.messages import AIMessage, ToolMessage

    from flowgentic.langchain import to_openai_messages

    messages = [
        AIMessage(
            "",
            tool_calls=[{"name": "f", "args": {"x": "Zürich"}, "id": "c1"}],
            invalid_tool_calls=[{"name": "g", "args": "{bad", "id": "c2", "error": "bad", "type": "invalid_tool_call"}],
        ),
        ToolMessage("r", tool_call_id="c1", name="f"),
        ToolMessage("error: bad", tool_call_id="c2", name="g", status="error"),
    ]
    assistant, *results = to_openai_messages(messages)
    calls = {c["id"]: c["function"] for c in assistant["tool_calls"]}
    assert calls == {"c1": {"name": "f", "arguments": '{"x": "Zürich"}'}, "c2": {"name": "g", "arguments": "{}"}}
    assert {r["tool_call_id"] for r in results} <= set(calls)
