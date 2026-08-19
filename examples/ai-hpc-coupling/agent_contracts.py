"""Framework-neutral agent decisions and hard policy for the AI-HPC demos."""

from __future__ import annotations

import math
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

Strategy = Literal["explore", "balanced", "exploit"]
Recommendation = Literal["continue", "stop"]


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
        fallback = evenly_spaced(lower_bound, upper_bound, expected_count)
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


def evenly_spaced(lower: float, upper: float, count: int) -> list[float]:
    """Return a deterministic bounded fallback batch."""
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
