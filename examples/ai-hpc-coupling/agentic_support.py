"""Agent models, structured decisions, and policy guards for the agentic demo."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar, cast

from campaign_types import CampaignState
from demo_support import agent_message, initial_campaign_state
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

Strategy = Literal["explore", "balanced", "exploit"]
Recommendation = Literal["continue", "stop"]
DecisionType = TypeVar("DecisionType", bound=BaseModel)


class PlannerDecision(BaseModel):
    """A planner agent's bounded experiment proposal."""

    candidates: list[float] = Field(
        min_length=1,
        description="Candidate coordinates selected for the next simulation batch.",
    )
    rationale: str = Field(description="Scientific reason for selecting this batch.")


class AnalysisDecision(BaseModel):
    """An analyst agent's interpretation of a surrogate update."""

    recommendation: Recommendation
    interpretation: str = Field(
        description="Concise interpretation of the new evidence and uncertainty."
    )


class SupervisorDecision(BaseModel):
    """A supervisor agent's recommendation for the next campaign cycle."""

    recommendation: Recommendation
    next_strategy: Strategy
    rationale: str = Field(
        description="Reason for the recommendation and next search strategy."
    )


class AgentDecisionModel(Protocol):
    """Decision interface shared by live and presentation-rehearsal agents."""

    name: str

    async def plan(self, context: dict[str, Any]) -> PlannerDecision: ...

    async def analyze(self, context: dict[str, Any]) -> AnalysisDecision: ...

    async def supervise(self, context: dict[str, Any]) -> SupervisorDecision: ...


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
                "You are the planner in a surrogate-guided scientific campaign. The surrogate has already been queried through your bounded tool. Select exactly expected_count candidate coordinates. Every coordinate must remain within lower_bound and upper_bound. Use the surrogate proposal as evidence, adapt it to the requested strategy when useful, and return only the structured decision."
            ),
            context,
        )

    async def analyze(self, context: dict[str, Any]) -> AnalysisDecision:
        return await self._decide(
            AnalysisDecision,
            (
                "You are the analyst in a surrogate-guided scientific campaign. Interpret the simulation results and the bounded surrogate update. Recommend stopping only if a stated hard stopping condition is met; otherwise recommend continuing. Return only the structured decision."
            ),
            context,
        )

    async def supervise(self, context: dict[str, Any]) -> SupervisorDecision:
        return await self._decide(
            SupervisorDecision,
            (
                "You supervise a planner and analyst in a scientific campaign. Choose explore, balanced, or exploit for the next cycle and reconcile their evidence. You may recommend stop, but application or ADR policy will enforce uncertainty and budget constraints. Return only the structured decision."
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
            candidates = _evenly_spaced(
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
                f"Use the {strategy} strategy with a surrogate-informed batch while respecting the fixed batch-size and search-bound policies."
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
                f"Coordinate the next cycle with a {strategy} strategy while ADR or application policy retains authority over stopping and budget."
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
    service: Any,
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


def enforce_candidate_policy(
    decision: PlannerDecision,
    fallback_candidates: list[float],
    *,
    expected_count: int,
    lower_bound: float,
    upper_bound: float,
) -> tuple[list[float], str]:
    """Validate an agent proposal and fall back to the surrogate if necessary."""
    candidates = [round(float(value), 6) for value in decision.candidates]
    if _candidate_batch_is_valid(
        candidates,
        expected_count=expected_count,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    ):
        return candidates, "agent proposal accepted"

    fallback = [round(float(value), 6) for value in fallback_candidates]
    fallback = fallback[:expected_count]
    if not _candidate_batch_is_valid(
        fallback,
        expected_count=expected_count,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    ):
        fallback = _evenly_spaced(lower_bound, upper_bound, expected_count)
    return fallback, "agent proposal rejected; surrogate fallback enforced"


def enforce_supervisor_policy(
    decision: SupervisorDecision,
    *,
    uncertainty: float,
    uncertainty_threshold: float,
    spent: int,
    budget: int,
) -> tuple[Recommendation, str]:
    """Keep stop authority with the hard scientific and resource policy."""
    hard_stop = uncertainty <= uncertainty_threshold or spent >= budget
    if hard_stop:
        status = (
            "supervisor agrees with hard stop"
            if decision.recommendation == "stop"
            else "hard stop overrides supervisor"
        )
        return "stop", status

    if decision.recommendation == "stop":
        return "continue", "premature agent stop rejected by hard policy"
    return "continue", "supervisor recommendation accepted"


def decision_message(agent: str, content: str, model_name: str) -> AIMessage:
    """Create an introspectable message for one agent decision."""
    return AIMessage(
        content=f"{agent}: {content}",
        response_metadata={"model_name": model_name},
        usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    )


def print_agentic_cycle(state: dict[str, Any], controller: str) -> None:
    """Print scientific progress and the three agent decisions for one cycle."""
    results = state["results"]
    retries = sum(max(0, int(result["attempt"]) - 1) for result in results)
    print(
        f"cycle={state['cycle']:>2}  controller={controller:<11}  "
        f"simulations={len(results)}  retries={retries}  "
        f"best_x={state['best_x']:.4f}  uncertainty={state['uncertainty']:.4f}  "
        f"budget={state['spent']}/{state['budget']}"
    )
    print(
        f"         agents: next={state['strategy']:<8} "
        f"recommendation={state['supervisor_recommendation']:<8} "
        f"planner={_shorten(state['planner_rationale'])}"
    )


def agent_trace_entry(
    *,
    agent: str,
    cycle: int,
    decision: dict[str, Any],
    policy_status: str,
) -> dict[str, Any]:
    """Create machine-readable evidence of an agent decision and its guardrail."""
    return {
        "agent": agent,
        "cycle": cycle,
        "decision": decision,
        "policy_status": policy_status,
    }


def deterministic_executor_message(count: int) -> AIMessage:
    """Describe deterministic execution without pretending it was LLM reasoning."""
    return agent_message(
        f"Tool executor launched {count} bounded simulations concurrently on compute."
    )


def _evenly_spaced(lower: float, upper: float, count: int) -> list[float]:
    if count <= 1:
        return [round((lower + upper) / 2, 6)]
    return [
        round(lower + (upper - lower) * index / (count - 1), 6)
        for index in range(count)
    ]


def _candidate_batch_is_valid(
    candidates: list[float],
    *,
    expected_count: int,
    lower_bound: float,
    upper_bound: float,
) -> bool:
    return (
        len(candidates) == expected_count
        and all(math.isfinite(value) for value in candidates)
        and all(lower_bound <= value <= upper_bound for value in candidates)
        and len({round(value, 10) for value in candidates}) == len(candidates)
    )


def _shorten(text: str, limit: int = 72) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"
