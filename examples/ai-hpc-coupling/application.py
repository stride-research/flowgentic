"""Flowgentic AI-HPC synthetic application demo.

Shows the scientific state, the Flowgentic execution mapping, and the agent
graph. Synthetic science, timing, reporting, and local demo setup live in
``demo_support.py``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict, cast

from demo_support import (SIMULATION_RETRY, SurrogateService,
                          add_instrumented_nodes, evaluate_candidate,
                          initial_campaign_state, print_cycle, record_analysis,
                          record_plan, record_simulations)
from langchain_core.messages import BaseMessage
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.graph.state import CompiledStateGraph

from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.langGraph.main import LangraphIntegration


# 1. Application state ---------------------------------------------------------
@dataclass(frozen=True)
class CampaignSettings:
    """Scientific and operational constraints for the campaign."""

    batch_size: int = 4
    budget: int = 24
    uncertainty_threshold: float = 0.25
    max_cycles: int = 8
    inject_failure: bool = True


class CampaignState(TypedDict):
    """Structured state exchanged by the three agent nodes."""

    messages: Annotated[list[BaseMessage], add_messages]
    cycle: int
    center: float
    radius: float
    uncertainty: float
    best_x: float | None
    best_value: float | None
    budget: int
    spent: int
    candidates: list[float]
    results: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    service: SurrogateService
    decision: str


@dataclass
class CampaignApplication:
    """The compiled agent graph and its persistent model service."""

    graph: CompiledStateGraph
    state: CampaignState
    service: SurrogateService
    service_future: asyncio.Future[SurrogateService]


async def build_campaign(
    integration: LangraphIntegration,
    settings: CampaignSettings,
) -> CampaignApplication:
    """Map one agentic AI-HPC campaign onto AsyncFlow through Flowgentic."""
    flowgentic = integration.execution_wrappers.asyncflow

    # 2. Scientific capabilities mapped to execution --------------------------
    # The surrogate is a persistent AI service: load it once and reuse it.
    @flowgentic(flow_type=AsyncFlowType.SERVICE_TASK, backend="ai")
    async def load_surrogate() -> SurrogateService:
        return await SurrogateService.load()

    service_future = await load_surrogate()
    service = await service_future

    # These are ordinary scientific capabilities. Flowgentic maps each one to
    # the execution semantics and backend appropriate for that capability.
    @flowgentic(flow_type=AsyncFlowType.FUNCTION_TASK, backend="ai")
    async def propose_candidates(
        center: float,
        radius: float,
        cycle: int,
    ) -> dict[str, Any]:
        return await service.propose(center, radius, settings.batch_size, cycle)

    @flowgentic(
        flow_type=AsyncFlowType.FUNCTION_TASK,
        backend="compute",
        retry=SIMULATION_RETRY,
    )
    async def simulate(
        x: float,
        cycle: int,
        candidate_index: int,
        fail_once: bool = False,
    ) -> dict[str, Any]:
        return await evaluate_candidate(x, cycle, candidate_index, fail_once)

    @flowgentic(flow_type=AsyncFlowType.FUNCTION_TASK, backend="ai")
    async def update_surrogate(
        results: list[dict[str, Any]],
        radius: float,
        cycle: int,
    ) -> dict[str, Any]:
        return await service.assimilate(results, radius, cycle)

    # 3. Agent nodes -----------------------------------------------------------
    # The graph keeps its own messages, state, and routing semantics.
    # Flowgentic only changes how its nodes and tools execute.
    async def plan(state: CampaignState) -> dict[str, Any]:
        proposal = await propose_candidates(
            state["center"], state["radius"], state["cycle"]
        )
        return record_plan(state, proposal)

    async def fan_out_simulations(state: CampaignState) -> dict[str, Any]:
        remaining = state["budget"] - state["spent"]
        candidates = state["candidates"][: max(0, remaining)]
        results = await asyncio.gather(
            *(
                simulate(
                    x,
                    state["cycle"],
                    index,
                    fail_once=(
                        settings.inject_failure
                        and state["cycle"] == 1
                        and index == 0
                    ),
                )
                for index, x in enumerate(candidates)
            )
        )
        return record_simulations(state, results)

    async def analyze(state: CampaignState) -> dict[str, Any]:
        update = await update_surrogate(
            state["results"], state["radius"], state["cycle"]
        )
        return record_analysis(state, update)

    # 4. Ordinary LangGraph composition ---------------------------------------
    graph = StateGraph(CampaignState)
    nodes = {
        "plan": flowgentic(flow_type=AsyncFlowType.EXECUTION_BLOCK)(plan),
        "simulate": flowgentic(flow_type=AsyncFlowType.EXECUTION_BLOCK)(
            fan_out_simulations
        ),
        "analyze": flowgentic(flow_type=AsyncFlowType.EXECUTION_BLOCK)(analyze),
    }
    add_instrumented_nodes(integration, graph, nodes)

    graph.add_edge(START, "plan")
    graph.add_edge("plan", "simulate")
    graph.add_edge("simulate", "analyze")
    graph.add_edge("analyze", END)

    state = cast(CampaignState, initial_campaign_state(service, settings.budget))
    return CampaignApplication(graph.compile(), state, service, service_future)


# 5. Application-owned campaign control --------------------------------------
async def run_with_application_control(
    application: CampaignApplication,
    settings: CampaignSettings,
) -> tuple[CampaignState, str]:
    """Iterate until the application reaches convergence or exhausts its budget."""
    state = application.state
    stop_reason = "maximum cycles reached"

    while state["cycle"] < settings.max_cycles and state["spent"] < state["budget"]:
        state = await application.graph.ainvoke(state)
        print_cycle(state, "application")
        if state["uncertainty"] <= settings.uncertainty_threshold:
            stop_reason = "scientific uncertainty goal reached"
            break
    else:
        if state["spent"] >= state["budget"]:
            stop_reason = "simulation budget exhausted"

    return state, stop_reason
