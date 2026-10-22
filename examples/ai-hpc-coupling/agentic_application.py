"""Agentic implementation of the same surrogate-guided AI-HPC campaign.

The planner and analyst interrogate a resident surrogate through bounded tools,
the tool executor launches simulations concurrently, and a supervisor
coordinates the next strategy. Application code or ADR retains hard authority
over convergence, budget, and maximum-cycle policies.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

from agentic_support import (
    AgentDecisionModel,
    AgenticCampaignState,
    AnalysisDecision,
    PlannerDecision,
    SupervisorDecision,
    agent_trace_entry,
    decision_message,
    deterministic_executor_message,
    enforce_candidate_policy,
    enforce_supervisor_policy,
    initial_agentic_state,
    print_agentic_cycle,
)
from campaign_types import CampaignApplication, CampaignSettings, CampaignState
from demo_support import (
    SIMULATION_RETRY,
    SurrogateService,
    add_instrumented_nodes,
    evaluate_candidate,
    timed_event,
)
from langgraph.graph import END, START, StateGraph

from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.langGraph.main import LangraphIntegration


async def build_agentic_campaign(
    integration: LangraphIntegration,
    settings: CampaignSettings,
    decision_model: AgentDecisionModel,
) -> CampaignApplication:
    """Build the LLM-agent implementation on the same Flowgentic resources."""
    flowgentic = integration.execution_wrappers.asyncflow

    # 1. The same resident surrogate service as the deterministic demo --------
    @flowgentic(flow_type=AsyncFlowType.SERVICE_TASK, backend="ai")
    async def load_surrogate() -> SurrogateService:
        return await SurrogateService.load()

    service_future = await load_surrogate()
    service = await service_future

    # 2. Bounded scientific capabilities exposed as agent tools ---------------
    @flowgentic(
        flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION,
        backend="ai",
        tool_description=(
            "Query the resident surrogate for a bounded candidate proposal."
        ),
    )
    async def query_surrogate(
        center: float,
        radius: float,
        cycle: int,
    ) -> dict[str, Any]:
        """Return the surrogate's candidate proposal for one campaign cycle."""
        return await service.propose(center, radius, settings.batch_size, cycle)

    @flowgentic(
        flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION,
        backend="compute",
        retry=SIMULATION_RETRY,
        tool_description="Run one approved scientific simulation on compute.",
    )
    async def run_simulation(
        x: float,
        cycle: int,
        candidate_index: int,
        fail_once: bool = False,
    ) -> dict[str, Any]:
        """Evaluate one policy-approved candidate."""
        return await evaluate_candidate(x, cycle, candidate_index, fail_once)

    @flowgentic(
        flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION,
        backend="ai",
        tool_description="Assimilate simulation evidence into the resident surrogate.",
    )
    async def update_surrogate(
        results: list[dict[str, Any]],
        radius: float,
        cycle: int,
    ) -> dict[str, Any]:
        """Update the surrogate with an approved simulation batch."""
        return await service.assimilate(results, radius, cycle)

    # Agent reasoning is independently placeable on the named AI backend.
    @flowgentic(flow_type=AsyncFlowType.FUNCTION_TASK, backend="ai")
    async def reason_about_plan(context: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        decision = await decision_model.plan(context)
        return {
            "decision": decision.model_dump(),
            "event": timed_event(
                name="agent.planner",
                backend="ai",
                cycle=int(context["cycle"]),
                started=started,
            ),
        }

    @flowgentic(flow_type=AsyncFlowType.FUNCTION_TASK, backend="ai")
    async def reason_about_evidence(context: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        decision = await decision_model.analyze(context)
        return {
            "decision": decision.model_dump(),
            "event": timed_event(
                name="agent.analyst",
                backend="ai",
                cycle=int(context["cycle"]),
                started=started,
            ),
        }

    @flowgentic(flow_type=AsyncFlowType.FUNCTION_TASK, backend="ai")
    async def coordinate_agents(context: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        decision = await decision_model.supervise(context)
        return {
            "decision": decision.model_dump(),
            "event": timed_event(
                name="agent.supervisor",
                backend="ai",
                cycle=int(context["cycle"]),
                started=started,
            ),
        }

    # 3. Planner agent: interrogate the surrogate, reason, then pass guardrails
    async def planner_agent(state: AgenticCampaignState) -> dict[str, Any]:
        remaining = state["budget"] - state["spent"]
        expected_count = min(settings.batch_size, remaining)
        lower_bound = state["center"] - state["radius"]
        upper_bound = state["center"] + state["radius"]

        proposal = await query_surrogate.ainvoke(
            {
                "center": state["center"],
                "radius": state["radius"],
                "cycle": state["cycle"],
            }
        )
        reasoning = await reason_about_plan(
            {
                "cycle": state["cycle"],
                "strategy": state["strategy"],
                "center": state["center"],
                "radius": state["radius"],
                "best_x": state["best_x"],
                "best_value": state["best_value"],
                "uncertainty": state["uncertainty"],
                "expected_count": expected_count,
                "lower_bound": lower_bound,
                "upper_bound": upper_bound,
                "surrogate_candidates": proposal["candidates"],
                "previous_results": state["results"],
            }
        )
        decision = PlannerDecision.model_validate(reasoning["decision"])
        candidates, policy_status = enforce_candidate_policy(
            decision,
            proposal["candidates"],
            expected_count=expected_count,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
        )

        return {
            "candidates": candidates,
            "planner_rationale": decision.rationale,
            "decision": f"planner selected {len(candidates)} candidates",
            "trace": [*state["trace"], proposal["event"], reasoning["event"]],
            "agent_trace": [
                *state["agent_trace"],
                agent_trace_entry(
                    agent="planner",
                    cycle=state["cycle"],
                    decision={
                        **decision.model_dump(),
                        "accepted_candidates": candidates,
                    },
                    policy_status=policy_status,
                ),
            ],
            "messages": [
                decision_message("planner", decision.rationale, decision_model.name)
            ],
        }

    # 4. Deterministic executor: invoke approved simulation tools concurrently -
    async def simulation_executor(
        state: AgenticCampaignState,
    ) -> dict[str, Any]:
        results = await asyncio.gather(
            *(
                run_simulation.ainvoke(
                    {
                        "x": x,
                        "cycle": state["cycle"],
                        "candidate_index": index,
                        "fail_once": (
                            settings.inject_failure
                            and state["cycle"] == 1
                            and index == 0
                        ),
                    }
                )
                for index, x in enumerate(state["candidates"])
            )
        )
        return {
            "results": results,
            "spent": state["spent"] + len(results),
            "trace": [*state["trace"], *(item["event"] for item in results)],
            "messages": [deterministic_executor_message(len(results))],
        }

    # 5. Analyst agent: update the model tool, then interpret its evidence -----
    async def analyst_agent(state: AgenticCampaignState) -> dict[str, Any]:
        update = await update_surrogate.ainvoke(
            {
                "results": state["results"],
                "radius": state["radius"],
                "cycle": state["cycle"],
            }
        )
        reasoning = await reason_about_evidence(
            {
                "cycle": state["cycle"],
                "results": state["results"],
                "surrogate_update": {
                    key: value for key, value in update.items() if key != "event"
                },
                "uncertainty_threshold": settings.uncertainty_threshold,
                "spent": state["spent"],
                "budget": state["budget"],
            }
        )
        decision = AnalysisDecision.model_validate(reasoning["decision"])
        next_cycle = state["cycle"] + 1

        return {
            "cycle": next_cycle,
            "center": update["best_x"],
            "radius": update["next_radius"],
            "uncertainty": update["uncertainty"],
            "best_x": update["best_x"],
            "best_value": update["best_value"],
            "analysis_rationale": decision.interpretation,
            "decision": decision.recommendation,
            "trace": [*state["trace"], update["event"], reasoning["event"]],
            "agent_trace": [
                *state["agent_trace"],
                agent_trace_entry(
                    agent="analyst",
                    cycle=next_cycle,
                    decision=decision.model_dump(),
                    policy_status="advisory; hard policy evaluated by controller",
                ),
            ],
            "messages": [
                decision_message(
                    "analyst", decision.interpretation, decision_model.name
                )
            ],
        }

    # 6. Supervisor agent: coordinate strategy, never override hard policy -----
    async def supervisor_agent(state: AgenticCampaignState) -> dict[str, Any]:
        reasoning = await coordinate_agents(
            {
                "cycle": state["cycle"],
                "planner_rationale": state["planner_rationale"],
                "analysis_rationale": state["analysis_rationale"],
                "current_strategy": state["strategy"],
                "best_x": state["best_x"],
                "best_value": state["best_value"],
                "uncertainty": state["uncertainty"],
                "uncertainty_threshold": settings.uncertainty_threshold,
                "spent": state["spent"],
                "budget": state["budget"],
            }
        )
        decision = SupervisorDecision.model_validate(reasoning["decision"])
        recommendation, policy_status = enforce_supervisor_policy(
            decision,
            uncertainty=state["uncertainty"],
            uncertainty_threshold=settings.uncertainty_threshold,
            spent=state["spent"],
            budget=state["budget"],
        )

        return {
            "strategy": decision.next_strategy,
            "supervisor_rationale": decision.rationale,
            "supervisor_recommendation": recommendation,
            "decision": recommendation,
            "trace": [*state["trace"], reasoning["event"]],
            "agent_trace": [
                *state["agent_trace"],
                agent_trace_entry(
                    agent="supervisor",
                    cycle=state["cycle"],
                    decision={
                        **decision.model_dump(),
                        "enforced_recommendation": recommendation,
                    },
                    policy_status=policy_status,
                ),
            ],
            "messages": [
                decision_message("supervisor", decision.rationale, decision_model.name)
            ],
        }

    # 7. The scientific loop is unchanged; only node implementation differs ---
    graph = StateGraph(AgenticCampaignState)
    nodes = {
        "planner_agent": flowgentic(flow_type=AsyncFlowType.EXECUTION_BLOCK)(
            planner_agent
        ),
        "simulation_executor": flowgentic(flow_type=AsyncFlowType.EXECUTION_BLOCK)(
            simulation_executor
        ),
        "analyst_agent": flowgentic(flow_type=AsyncFlowType.EXECUTION_BLOCK)(
            analyst_agent
        ),
        "supervisor_agent": flowgentic(flow_type=AsyncFlowType.EXECUTION_BLOCK)(
            supervisor_agent
        ),
    }
    add_instrumented_nodes(integration, graph, nodes)
    graph.add_edge(START, "planner_agent")
    graph.add_edge("planner_agent", "simulation_executor")
    graph.add_edge("simulation_executor", "analyst_agent")
    graph.add_edge("analyst_agent", "supervisor_agent")
    graph.add_edge("supervisor_agent", END)

    state = initial_agentic_state(service, settings.budget, decision_model.name)
    return CampaignApplication(
        graph.compile(),
        cast(CampaignState, state),
        service,
        service_future,
    )


async def run_agentic_with_application_control(
    application: CampaignApplication,
    settings: CampaignSettings,
) -> tuple[CampaignState, str]:
    """Apply the same hard stopping policy around the agentic graph."""
    state = application.state
    stop_reason = "maximum cycles reached"

    while state["cycle"] < settings.max_cycles and state["spent"] < state["budget"]:
        state = await application.graph.ainvoke(state)
        print_agentic_cycle(state, "application")
        if state["uncertainty"] <= settings.uncertainty_threshold:
            stop_reason = "scientific uncertainty goal reached"
            break
    else:
        if state["spent"] >= state["budget"]:
            stop_reason = "simulation budget exhausted"

    return state, stop_reason
