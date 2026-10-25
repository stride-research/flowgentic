"""The agentic AI-HPC campaign on AsyncFlow directly, without Flowgentic.

This is ``agentic_application.py`` with the Flowgentic layer removed: every
``flowgentic`` decorator becomes a direct AsyncFlow call, and the LangGraph
graph, node bodies, tools, state, and policy guards are unchanged. The runner
selects it with ``agentic_campaign.py --integration direct``; the substitution
experiment (``docs/evaluation/substitution.md``) compares the two versions.
"""

from __future__ import annotations

import asyncio
import time
from functools import wraps
from typing import Any, Awaitable, Callable, cast

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
)
from campaign_common import (
    SurrogateService,
    evaluate_candidate,
    retry_transient_simulation,
    timed_event,
)
from campaign_types import CampaignApplication, CampaignSettings, CampaignState
from langchain_core.tools import BaseTool, tool
from langgraph.graph import END, START, StateGraph
from radical.asyncflow import WorkflowEngine


async def build_direct_agentic_campaign(
    flow: WorkflowEngine,
    settings: CampaignSettings,
    decision_model: AgentDecisionModel,
) -> CampaignApplication:
    """Build the same LLM-agent implementation directly on AsyncFlow."""

    # --8<-- [start:direct-service]
    # 1. The same resident surrogate service as the deterministic demo --------
    @flow.function_task(service=True, backend="ai")
    async def load_surrogate() -> SurrogateService:
        return await SurrogateService.load()

    service_future = load_surrogate()
    service = await service_future
    # --8<-- [end:direct-service]

    # --8<-- [start:direct-tools]
    # 2. Bounded scientific capabilities exposed as agent tools ---------------
    @asyncflow_tool("Query the resident surrogate for a bounded candidate proposal.")
    @flow.function_task(backend="ai")
    async def query_surrogate(
        center: float,
        radius: float,
        cycle: int,
    ) -> dict[str, Any]:
        """Return the surrogate's candidate proposal for one campaign cycle."""
        return await service.propose(center, radius, settings.batch_size, cycle)

    @asyncflow_tool(
        "Run one approved scientific simulation on compute.",
        retry=retry_transient_simulation,
    )
    @flow.function_task(backend="compute")
    async def run_simulation(
        x: float,
        cycle: int,
        candidate_index: int,
        fail_once: bool = False,
    ) -> dict[str, Any]:
        """Evaluate one policy-approved candidate."""
        return await evaluate_candidate(x, cycle, candidate_index, fail_once)

    @asyncflow_tool("Assimilate simulation evidence into the resident surrogate.")
    @flow.function_task(backend="ai")
    async def update_surrogate(
        results: list[dict[str, Any]],
        radius: float,
        cycle: int,
    ) -> dict[str, Any]:
        """Update the surrogate with an approved simulation batch."""
        return await service.assimilate(results, radius, cycle)
    # --8<-- [end:direct-tools]

    # Agent reasoning is independently placeable on the named AI backend.
    @flow.function_task(backend="ai")
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

    @flow.function_task(backend="ai")
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

    @flow.function_task(backend="ai")
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
                decision_message("planner", decision.rationale)
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
                decision_message("analyst", decision.interpretation)
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
                decision_message("supervisor", decision.rationale)
            ],
        }

    # --8<-- [start:direct-blocks]
    # 7. The scientific loop is unchanged; only node implementation differs ---
    graph = StateGraph(AgenticCampaignState)
    nodes = {
        "planner_agent": asyncflow_block(flow, planner_agent),
        "simulation_executor": asyncflow_block(flow, simulation_executor),
        "analyst_agent": asyncflow_block(flow, analyst_agent),
        "supervisor_agent": asyncflow_block(flow, supervisor_agent),
    }
    for name, node in nodes.items():
        graph.add_node(name, node)
    graph.add_edge(START, "planner_agent")
    graph.add_edge("planner_agent", "simulation_executor")
    graph.add_edge("simulation_executor", "analyst_agent")
    graph.add_edge("analyst_agent", "supervisor_agent")
    graph.add_edge("supervisor_agent", END)
    # --8<-- [end:direct-blocks]

    state = initial_agentic_state(service, settings.budget, decision_model.name)
    return CampaignApplication(
        graph.compile(),
        cast(CampaignState, state),
        service,
        service_future,
    )


# --8<-- [start:direct-helpers]
# 8. What the Flowgentic decorators did, written directly against AsyncFlow ---
def asyncflow_tool(
    description: str,
    *,
    retry: Callable[[Callable[[], Awaitable[Any]]], Awaitable[Any]] | None = None,
) -> Callable[[Callable[..., Any]], BaseTool]:
    """Expose an AsyncFlow task as a LangChain tool, with an optional retry."""

    def decorate(task: Callable[..., Any]) -> BaseTool:
        @wraps(task)
        async def invoke(*args: Any, **kwargs: Any) -> Any:
            if retry is None:
                return await task(*args, **kwargs)
            return await retry(lambda: task(*args, **kwargs))

        return tool(invoke, description=description)

    return decorate


def asyncflow_block(
    flow: WorkflowEngine,
    node: Callable[[Any], Awaitable[dict[str, Any]]],
) -> Callable[[Any], Awaitable[dict[str, Any]]]:
    """Run a LangGraph node as an AsyncFlow block."""
    block = flow.block(node)

    @wraps(node)
    async def run_block(state: Any) -> dict[str, Any]:
        return await block(state)

    return run_block
# --8<-- [end:direct-helpers]
