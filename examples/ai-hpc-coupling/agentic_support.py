"""Agent models, structured decisions, and policy guards for the agentic demo."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from agent_contracts import (
    AgentDecisionModel,
    AnalysisDecision,
    PlannerDecision,
    Recommendation,
    Strategy,
    SupervisorDecision,
    agent_trace_entry,
    enforce_candidate_policy,
    enforce_supervisor_policy,
    evenly_spaced,
    print_agentic_cycle,
)
from campaign_common import SurrogateService
from campaign_types import CampaignState
from demo_support import agent_message, initial_campaign_state
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

DecisionType = TypeVar("DecisionType", bound=BaseModel)


@dataclass
class LangChainDecisionModel:
    """LLM-backed planner, analyst, and supervisor with structured output."""

    model: BaseChatModel
    name: str

    async def _decide(
        self,
        schema: type[DecisionType],
        system_prompt: str,
        context: dict[str, Any],
    ) -> DecisionType:
        structured_model = self.model.with_structured_output(schema)
        response = await structured_model.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=json.dumps(context, indent=2, sort_keys=True)),
            ]
        )
        if isinstance(response, schema):
            return response
        return schema.model_validate(response)

    async def plan(self, context: dict[str, Any]) -> PlannerDecision:
        return await self._decide(
            PlannerDecision,
            (
                "You are the planner in a surrogate-guided scientific campaign. "
                "The surrogate has already been queried through your bounded "
                "tool. Select exactly expected_count candidate coordinates. "
                "Every coordinate must remain within lower_bound and upper_bound. "
                "Use the surrogate proposal as evidence, adapt it to the requested "
                "strategy when useful, and return only the structured decision."
            ),
            context,
        )

    async def analyze(self, context: dict[str, Any]) -> AnalysisDecision:
        return await self._decide(
            AnalysisDecision,
            (
                "You are the analyst in a surrogate-guided scientific campaign. "
                "Interpret the simulation results and the bounded surrogate "
                "update. Recommend stopping only if a stated hard stopping "
                "condition is met; otherwise recommend continuing. Return only "
                "the structured decision."
            ),
            context,
        )

    async def supervise(self, context: dict[str, Any]) -> SupervisorDecision:
        return await self._decide(
            SupervisorDecision,
            (
                "You supervise a planner and analyst in a scientific campaign. "
                "Choose explore, balanced, or exploit for the next cycle and "
                "reconcile their evidence. You may recommend stop, but application "
                "or ADR policy will enforce uncertainty and budget constraints. "
                "Return only the structured decision."
            ),
            context,
        )


@dataclass
class RehearsalDecisionModel:
    """Scripted agent decisions for reliable offline presentation rehearsals."""

    name: str = "rehearsal-agents"

    async def plan(self, context: dict[str, Any]) -> PlannerDecision:
        expected = int(context["expected_count"])
        lower = float(context["lower_bound"])
        upper = float(context["upper_bound"])
        strategy = cast(Strategy, context["strategy"])
        best_x = context.get("best_x")

        if strategy == "exploit" and best_x is not None:
            center = float(best_x)
            half_width = 0.35 * (upper - lower) / 2
            candidates = evenly_spaced(
                max(lower, center - half_width),
                min(upper, center + half_width),
                expected,
            )
        else:
            candidates = [
                float(value) for value in context["surrogate_candidates"][:expected]
            ]

        return PlannerDecision(
            candidates=candidates,
            rationale=(
                f"Use the {strategy} strategy with a surrogate-informed batch "
                "while respecting the fixed batch-size and search-bound policies."
            ),
        )

    async def analyze(self, context: dict[str, Any]) -> AnalysisDecision:
        uncertainty = float(context["surrogate_update"]["uncertainty"])
        threshold = float(context["uncertainty_threshold"])
        recommendation: Recommendation = (
            "stop" if uncertainty <= threshold else "continue"
        )
        return AnalysisDecision(
            recommendation=recommendation,
            interpretation=(
                f"The new evidence reduces uncertainty to {uncertainty:.4f}; "
                f"the hard convergence threshold is {threshold:.4f}."
            ),
        )

    async def supervise(self, context: dict[str, Any]) -> SupervisorDecision:
        uncertainty = float(context["uncertainty"])
        threshold = float(context["uncertainty_threshold"])
        spent = int(context["spent"])
        budget = int(context["budget"])

        if uncertainty <= threshold or spent >= budget:
            recommendation: Recommendation = "stop"
        else:
            recommendation = "continue"

        if uncertainty > 1.0:
            strategy: Strategy = "explore"
        elif uncertainty > 0.4:
            strategy = "balanced"
        else:
            strategy = "exploit"

        return SupervisorDecision(
            recommendation=recommendation,
            next_strategy=strategy,
            rationale=(
                f"Coordinate the next cycle with a {strategy} strategy while ADR "
                "or application policy retains authority over stopping and budget."
            ),
        )


class AgenticCampaignState(CampaignState):
    """Campaign state extended with explicit agent decisions."""

    agent_model: str
    strategy: Strategy
    planner_rationale: str
    analysis_rationale: str
    supervisor_rationale: str
    supervisor_recommendation: Recommendation
    agent_trace: list[dict[str, Any]]


def initial_agentic_state(
    service: SurrogateService,
    budget: int,
    model_name: str,
) -> AgenticCampaignState:
    """Extend the shared initial state with agent coordination fields."""
    state = initial_campaign_state(service, budget)
    state.update(
        {
            "implementation": "agentic",
            "agent_model": model_name,
            "strategy": "balanced",
            "planner_rationale": "Campaign has not yet been planned.",
            "analysis_rationale": "No simulation evidence is available.",
            "supervisor_rationale": "Supervisor is awaiting the first cycle.",
            "supervisor_recommendation": "continue",
            "agent_trace": [],
        }
    )
    return cast(AgenticCampaignState, state)


def decision_message(agent: str, content: str, model_name: str) -> AIMessage:
    """Create an introspectable message for one agent decision."""
    return AIMessage(
        content=f"{agent}: {content}",
        response_metadata={"model_name": model_name},
        usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    )


def deterministic_executor_message(count: int) -> AIMessage:
    """Describe deterministic execution without pretending it was LLM reasoning."""
    return agent_message(
        f"Tool executor launched {count} bounded simulations concurrently on compute."
    )
