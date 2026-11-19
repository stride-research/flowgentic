"""Shared pieces for the tests of the agent host, its runners and the tools."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from flowgentic.langgraph import route_tool_calls

RUNS: dict[str, int] = {}
_RUNS_LOCK = threading.Lock()


def count(name: str) -> None:
    with _RUNS_LOCK:
        RUNS[name] = RUNS.get(name, 0) + 1


def simulate(x: float) -> dict:
    """Run a simulation of x."""
    count("simulate")
    return {"y": 2 * x}


def broken(x: float) -> dict:
    """A simulation that fails."""
    count("broken")
    raise ValueError(f"no convergence at {x}")


async def thread_session(workers: int = 8):
    """A RHAPSODY session on a thread pool, as a laptop runs one."""
    from rhapsody import Session
    from rhapsody.backends import ConcurrentExecutionBackend

    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor(max_workers=workers))
    return Session(backends=[backend])


class Scripted(BaseChatModel):
    """A chat model that follows ``script(messages) -> AIMessage``."""

    script: Callable[[list[BaseMessage]], AIMessage]

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Scripted:
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self.script(messages))])


def calls_then_answer(*calls: tuple[str, dict]) -> Callable[[list[BaseMessage]], AIMessage]:
    """First turn: the given tool calls, at once. Then answer with the tools'
    results, joined."""

    def script(messages: list[BaseMessage]) -> AIMessage:
        results = [m for m in messages if m.type == "tool"]
        if not results:
            return AIMessage(
                "",
                tool_calls=[
                    {"name": name, "args": args, "id": f"call-{i}"}
                    for i, (name, args) in enumerate(calls)
                ],
            )
        return AIMessage(" | ".join(str(m.content) for m in results))

    return script


def react_graph(model: Any, tools: list, saver: Any = None, *, handle_tool_errors: Any = True):
    """model -> each tool call as its own task -> model, until no tool call.
    ``handle_tool_errors=None`` keeps ToolNode's default, which re-raises a
    tool's own errors."""
    if handle_tool_errors is None:
        node = ToolNode(tools)
    else:
        node = ToolNode(tools, handle_tool_errors=handle_tool_errors)

    async def call_model(state: MessagesState):
        return {"messages": [await model.ainvoke(state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("model", call_model)
    g.add_node("tools", node)
    g.add_edge(START, "model")
    g.add_conditional_edges("model", route_tool_calls, ["tools", "__end__"])
    g.add_edge("tools", "model")
    return g.compile(checkpointer=saver)


# --- transports: OpenAI-format messages, no LangChain ------------------------------


def reply(content="", calls=(), usage=(10, 2), reasoning=None, cached=None, thinking=None):
    """A Reply: ``calls`` are (id, name, arguments) triples, the arguments a
    dict, or a string that becomes an invalid call."""
    from flowgentic.fleet import InvalidToolCall, Reply, ToolCall, Usage

    valid = tuple(ToolCall(i, n, a) for i, n, a in calls if isinstance(a, dict))
    invalid = tuple(
        InvalidToolCall(i, n, a, "not valid JSON") for i, n, a in calls if isinstance(a, str)
    )
    return Reply(
        content=content,
        tool_calls=valid,
        invalid_tool_calls=invalid,
        usage=None if usage is None else Usage(usage[0], usage[1], cached, thinking),
        reasoning=reasoning,
        finish_reason="tool_calls" if calls else "stop",
        id="r-1",
        model="scripted",
    )


class ScriptedTransport:
    """A transport that answers ``script(messages)``: a Reply, or a string for
    a plain answer. It keeps each call's messages, tools and keywords."""

    def __init__(self, script, model="scripted", base_url=None, latency_s=0.0):
        self.script = script
        self.model = model
        self.base_url = base_url
        self.latency_s = latency_s
        self.calls = []

    async def complete(self, messages, tools=None, **kwargs):
        self.calls.append((list(messages), tools, kwargs))
        if self.latency_s:
            import asyncio

            await asyncio.sleep(self.latency_s)
        out = self.script(list(messages))
        return reply(out) if isinstance(out, str) else out


def tool_results(messages):
    return [m for m in messages if m["role"] == "tool"]


def dict_calls_then_answer(*calls):
    """First turn: the given tool calls, at once; then answer with the tools'
    results, joined. On OpenAI-format messages."""

    def script(messages):
        results = tool_results(messages)
        if not results:
            return reply(calls=[(f"call-{i}", name, args) for i, (name, args) in enumerate(calls)])
        return reply(" | ".join(str(m["content"]) for m in results))

    return script
