"""Shared pieces for the tests of the agent host, its runners and the tools,
with LangChain's: ``_basics`` and a scripted chat model and graph."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from flowgentic.langgraph import route_tool_calls

from _basics import (  # noqa: F401  # LangChain's tests use them too
    RUNS,
    ScriptedTransport,
    broken,
    count,
    dict_calls_then_answer,
    reply,
    simulate,
    thread_session,
    tool_results,
)


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
