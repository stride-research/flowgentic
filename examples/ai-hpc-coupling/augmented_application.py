"""Agent-augmented RADICAL application with AsyncFlow-owned orchestration.

All scientific services, simulations, fan-out, retries, and campaign state use
ordinary Python and AsyncFlow.  Flowgentic inserts replaceable agent-framework
components only at the planner, analyst, and supervisor decision points.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from agent_contracts import (
    AnalysisDecision,
    PlannerDecision,
    SupervisorDecision,
    agent_trace_entry,
    enforce_candidate_policy,
    enforce_supervisor_policy,
    print_agentic_cycle,
)
from campaign_common import (
    CampaignSettings,
    SurrogateService,
    evaluate_candidate,
    initial_scientific_state,
    retry_transient_simulation,
)
from radical.asyncflow import WorkflowEngine

from flowgentic.agent import AgentComponent, bind_agent

CampaignState = dict[str, Any]
Cycle = Callable[[CampaignState], Awaitable[CampaignState]]


class CampaignAgentComponents(Protocol):
    """Framework-neutral component bundle supplied by demo scaffolding."""

    framework: str
    model_name: str
    planner: AgentComponent
    analyst: AgentComponent
    supervisor: AgentComponent


@dataclass
class AugmentedApplication:
    """An AsyncFlow application augmented by three portable agent components."""

    state: CampaignState
    service: SurrogateService
    service_future: asyncio.Future[SurrogateService]
    run_cycle: Cycle


async def build_augmented_campaign(
    flow: WorkflowEngine,
    settings: CampaignSettings,
    components: CampaignAgentComponents,
) -> AugmentedApplication:
    """Insert agent decisions into an otherwise direct AsyncFlow campaign."""

    # 1. Existing RADICAL services and tasks remain direct AsyncFlow code.
    @flow.function_task(service=True, backend="ai")
    async def load_surrogate() -> SurrogateService:
        return await SurrogateService.load()

    service_future = load_surrogate()
    service = await service_future

    @flow.function_task(backend="ai")
    async def propose_candidates(
        center: float,
        radius: float,
        cycle: int,
    ) -> dict[str, Any]:
        return await service.propose(center, radius, settings.batch_size, cycle)

    @flow.function_task(backend="compute")
    async def simulate(
        x: float,
        cycle: int,
        candidate_index: int,
        fail_once: bool = False,
    ) -> dict[str, Any]:
        return await evaluate_candidate(x, cycle, candidate_index, fail_once)

    @flow.function_task(backend="ai")
    async def update_surrogate(
        results: list[dict[str, Any]],
        radius: float,
        cycle: int,
    ) -> dict[str, Any]:
        return await service.assimilate(results, radius, cycle)

    # 2. Only agent components cross the Flowgentic/framework boundary.
    planner = bind_agent(
        flow,
        components.planner,
        identity="planner",
        framework=components.framework,
        backend="ai",
    )
    analyst = bind_agent(
        flow,
        components.analyst,
        identity="analyst",
        framework=components.framework,
        backend="ai",
    )
    supervisor = bind_agent(
        flow,
        components.supervisor,
        identity="supervisor",
        framework=components.framework,
        backend="ai",
    )

    # 3. The application still owns the complete scientific cycle.
    async def run_cycle(state: CampaignState) -> CampaignState:
        remaining = state["budget"] - state["spent"]
        expected_count = min(settings.batch_size, remaining)
        lower_bound = state["center"] - state["radius"]
        upper_bound = state["center"] + state["radius"]

        proposal = await propose_candidates(
            state["center"], state["radius"], state["cycle"]
        )
        plan = await planner.ainvoke(
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
        planner_decision = PlannerDecision.model_validate(plan["decision"])
        candidates, candidate_policy = enforce_candidate_policy(
            planner_decision,
            proposal["candidates"],
            expected_count=expected_count,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
        )

        results = await asyncio.gather(
            *(
                retry_transient_simulation(
                    lambda x=x, index=index: simulate(
                        x,
                        state["cycle"],
                        index,
                        fail_once=(
                            settings.inject_failure
                            and state["cycle"] == 1
                            and index == 0
                        ),
                    )
                )
                for index, x in enumerate(candidates)
            )
        )
        update = await update_surrogate(results, state["radius"], state["cycle"])

        analysis = await analyst.ainvoke(
            {
                "cycle": state["cycle"],
                "results": results,
                "surrogate_update": {
                    key: value for key, value in update.items() if key != "event"
                },
                "uncertainty_threshold": settings.uncertainty_threshold,
                "spent": state["spent"] + len(results),
                "budget": state["budget"],
            }
        )
        analyst_decision = AnalysisDecision.model_validate(analysis["decision"])

        next_cycle = state["cycle"] + 1
        next_spent = state["spent"] + len(results)
        supervision = await supervisor.ainvoke(
            {
                "cycle": next_cycle,
                "planner_rationale": planner_decision.rationale,
                "analysis_rationale": analyst_decision.interpretation,
                "analysis_recommendation": analyst_decision.recommendation,
                "current_strategy": state["strategy"],
                "best_x": update["best_x"],
                "best_value": update["best_value"],
                "uncertainty": update["uncertainty"],
                "uncertainty_threshold": settings.uncertainty_threshold,
                "spent": next_spent,
                "budget": state["budget"],
            }
        )
        supervisor_decision = SupervisorDecision.model_validate(
            supervision["decision"]
        )
        recommendation, supervisor_policy = enforce_supervisor_policy(
            supervisor_decision,
            uncertainty=update["uncertainty"],
            uncertainty_threshold=settings.uncertainty_threshold,
            spent=next_spent,
            budget=state["budget"],
        )

        return {
            **state,
            "cycle": next_cycle,
            "center": update["best_x"],
            "radius": update["next_radius"],
            "uncertainty": update["uncertainty"],
            "best_x": update["best_x"],
            "best_value": update["best_value"],
            "spent": next_spent,
            "candidates": candidates,
            "results": results,
            "strategy": supervisor_decision.next_strategy,
            "planner_rationale": planner_decision.rationale,
            "analysis_rationale": analyst_decision.interpretation,
            "supervisor_rationale": supervisor_decision.rationale,
            "supervisor_recommendation": recommendation,
            "decision": recommendation,
            "trace": [
                *state["trace"],
                proposal["event"],
                plan["event"],
                *(result["event"] for result in results),
                update["event"],
                analysis["event"],
                supervision["event"],
            ],
            "agent_trace": [
                *state["agent_trace"],
                agent_trace_entry(
                    agent="planner",
                    cycle=state["cycle"],
                    decision={
                        **planner_decision.model_dump(),
                        "accepted_candidates": candidates,
                    },
                    policy_status=candidate_policy,
                ),
                agent_trace_entry(
                    agent="analyst",
                    cycle=next_cycle,
                    decision=analyst_decision.model_dump(),
                    policy_status="advisory; application retains scientific state",
                ),
                agent_trace_entry(
                    agent="supervisor",
                    cycle=next_cycle,
                    decision={
                        **supervisor_decision.model_dump(),
                        "enforced_recommendation": recommendation,
                    },
                    policy_status=supervisor_policy,
                ),
            ],
        }

    state = initial_scientific_state(
        service,
        settings.budget,
        implementation="agent-augmented-radical",
    )
    state.update(
        {
            "agent_framework": components.framework,
            "agent_model": components.model_name,
            "strategy": "balanced",
            "planner_rationale": "Campaign has not yet been planned.",
            "analysis_rationale": "No simulation evidence is available.",
            "supervisor_rationale": "Supervisor is awaiting the first cycle.",
            "supervisor_recommendation": "continue",
            "agent_trace": [],
        }
    )
    return AugmentedApplication(state, service, service_future, run_cycle)


async def run_augmented_with_application_control(
    application: AugmentedApplication,
    settings: CampaignSettings,
) -> tuple[CampaignState, str]:
    """Apply hard campaign policy around the agent-augmented AsyncFlow cycle."""
    state = application.state
    stop_reason = "maximum cycles reached"

    while state["cycle"] < settings.max_cycles and state["spent"] < state["budget"]:
        state = await application.run_cycle(state)
        print_agentic_cycle(state, "application")
        if state["uncertainty"] <= settings.uncertainty_threshold:
            stop_reason = "scientific uncertainty goal reached"
            break
    else:
        if state["spent"] >= state["budget"]:
            stop_reason = "simulation budget exhausted"

    return state, stop_reason
