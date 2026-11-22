"""The agents-at-scale campaign's LangGraph version: the same agents, tools and
parts as ``campaign.py``, as a LangGraph graph run by ``GraphRunner``, with
the checkpoints in ``KeyValueSaver``. It needs the ``langgraph`` extra.

    python examples/agents-at-scale/campaign.py --runner graph --agents 1000

``campaign.py`` loads it, and passes it the fleet, the runtime, the store and
the tools' declarations.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from flowgentic.fleet import Fleet
from flowgentic.langchain import to_ai_message
from flowgentic.langgraph import (
    GraphRunner,
    KeyValueSaver,
    bind_tools,
    route_tool_calls,
)
from flowgentic.store import KeyValueStore
from flowgentic.tools import McpTool, TaskTool, ToolRuntime

# --- a scripted model, standing in for the model servers -----------------------


class ScriptedChatModel(BaseChatModel):
    """The campaign's scripted model as a LangChain chat model: it looks the
    candidate up; simulates it at the reference temperature and notes that, in
    one turn; then answers with the score. On an allocation each replica is a
    chat model of a model server, or an ``HTTPTransport``."""

    latency_s: float = 0.005

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError("the scripted model is asynchronous")

    async def _agenerate(
        self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs
    ) -> ChatResult:
        await asyncio.sleep(self.latency_s)
        candidate = messages[0].content.split()[-1]
        done = {m.name: m.content for m in messages if m.type == "tool"}
        if "lookup" not in done:
            reply = AIMessage(
                "",
                tool_calls=[
                    {"name": "lookup", "args": {"candidate": candidate}, "id": "c1"}
                ],
            )
        elif "simulate" not in done:
            start = json.loads(done["lookup"])["start_temperature"]
            reply = AIMessage(
                "",
                tool_calls=[
                    {
                        "name": "simulate",
                        "args": {"candidate": candidate, "temperature": start},
                        "id": "c2",
                    },
                    {
                        "name": "note",
                        "args": {"text": f"simulating {candidate}"},
                        "id": "c3",
                    },
                ],
            )
        else:
            reply = AIMessage(f"best score {json.loads(done['simulate'])['score']}")
        # What a server would count: the prompt's size, and a short reply.
        prompt = 200 + sum(len(str(m.content)) for m in messages) // 4
        reply.usage_metadata = {
            "input_tokens": prompt,
            "output_tokens": 20,
            "total_tokens": prompt + 20,
        }
        return ChatResult(generations=[ChatGeneration(message=reply)])


def replicas(latency_s: float = 0.005) -> list[ScriptedChatModel]:
    """The fleet's two replicas, each call taking ``latency_s``."""
    return [ScriptedChatModel(latency_s=latency_s), ScriptedChatModel(latency_s=latency_s)]


# --- the graph -------------------------------------------------------------------


# --8<-- [start:graph]
def build_graph(fleet: Fleet, tools: list, saver: KeyValueSaver):
    """model -> each tool call as its own task -> model, until it answers.

    The replicas are LangChain chat models, so the fleet passes the graph's
    messages and tools to them as they are, and the reply's ``raw`` message is
    the model's own ``AIMessage``."""

    async def model(state: MessagesState, config: RunnableConfig) -> dict:
        agent = config["configurable"]["thread_id"]
        reply = await fleet.ainvoke(agent, state["messages"], tools=tools)
        return {"messages": [to_ai_message(reply)]}

    g = StateGraph(MessagesState)
    g.add_node("model", model)
    g.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    g.add_edge(START, "model")
    g.add_conditional_edges("model", route_tool_calls, ["tools", "__end__"])
    g.add_edge("tools", "model")
    return g.compile(checkpointer=saver)
# --8<-- [end:graph]


def graph_runner(
    fleet: Fleet, runtime: ToolRuntime, store: KeyValueStore, tools: list
) -> tuple[GraphRunner, KeyValueSaver]:
    """The runner of the campaign's graph, and its checkpointer. Task and MCP
    tools run on ``runtime`` through ``bind_tools``; a plain function becomes a
    LangChain tool."""
    # --8<-- [start:runner]
    saver = KeyValueSaver(store)
    bound = bind_tools(
        runtime, [t if isinstance(t, (TaskTool, McpTool)) else tool(t) for t in tools]
    )
    # A Dragon dictionary's size is fixed: keep the agents still running, not
    # every agent of the campaign.
    runner = GraphRunner(build_graph(fleet, bound, saver), delete_finished=True)
    # --8<-- [end:runner]
    return runner, saver
