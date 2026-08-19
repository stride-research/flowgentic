"""LangGraph implementations of the agent roles used by the augmented demo.

Only this module depends on LangGraph.  The RADICAL application invokes these
components through Flowgentic without expressing its scientific workflow as a
LangGraph graph.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypedDict

from agent_contracts import AgentDecisionModel
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel


class ComponentState(TypedDict, total=False):
    """Private state for one small agent-framework component."""

    context: dict[str, Any]
    decision: dict[str, Any]


@dataclass(frozen=True)
class LangGraphAgentComponent:
    """A role-specific LangGraph hidden behind Flowgentic's agent interface."""

    identity: str
    graph: CompiledStateGraph

    async def ainvoke(self, context: Mapping[str, Any]) -> dict[str, Any]:
        """Run the component's private graph and return its structured decision."""
        result = await self.graph.ainvoke({"context": dict(context)})
        return result["decision"]


@dataclass(frozen=True)
class AgentComponents:
    """The three replaceable agent-framework components in the demo."""

    planner: LangGraphAgentComponent
    analyst: LangGraphAgentComponent
    supervisor: LangGraphAgentComponent
    model_name: str
    framework: str = "langgraph"


def build_langgraph_agent_components(
    decision_model: AgentDecisionModel,
) -> AgentComponents:
    """Build private LangGraph components for the three agent roles."""
    return AgentComponents(
        planner=_build_component("planner", decision_model.plan),
        analyst=_build_component("analyst", decision_model.analyze),
        supervisor=_build_component("supervisor", decision_model.supervise),
        model_name=decision_model.name,
    )


def _build_component(
    identity: str,
    decide: Callable[[dict[str, Any]], Awaitable[BaseModel]],
) -> LangGraphAgentComponent:
    graph = StateGraph(ComponentState)

    async def reason(state: ComponentState) -> dict[str, Any]:
        decision = await decide(state["context"])
        return {"decision": decision.model_dump()}

    graph.add_node("reason", reason)
    graph.add_edge(START, "reason")
    graph.add_edge("reason", END)
    return LangGraphAgentComponent(identity, graph.compile())
