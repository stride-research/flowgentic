"""Framework-neutral agent decisions and hard policy for the AI-HPC demos."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from flowgentic.acceptance import Rejected

Strategy = Literal["explore", "balanced", "exploit"]
Recommendation = Literal["continue", "stop"]

# A planner proposal that fails a check is asked for once more
PLANNER_ATTEMPTS = 2

PLANNER_POLICY_STATUS = {
    "accepted": "agent proposal accepted",
    "fallback": "agent proposal rejected; surrogate fallback enforced",
}


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


# --8<-- [start:planner-checks]
def planner_checks(
    *,
    expected_count: int,
    lower_bound: float,
    upper_bound: float,
) -> list[Callable[[PlannerDecision], None]]:
    """Return the planner's checks, cheapest first; each raises ``Rejected``."""

    def batch_size(decision: PlannerDecision) -> None:
        count = len(decision.candidates)
        if count != expected_count:
            raise Rejected(
                f"{count} candidates; the batch takes exactly {expected_count}"
            )

    def within_bounds(decision: PlannerDecision) -> None:
        # A NaN fails both comparisons, so it lies outside too
        outside = [
            value
            for value in decision.candidates
            if not lower_bound <= value <= upper_bound
        ]
        if outside:
            raise Rejected(f"{outside} lie outside [{lower_bound}, {upper_bound}]")

    def distinct(decision: PlannerDecision) -> None:
        unique = {round(value, 10) for value in decision.candidates}
        if len(unique) != len(decision.candidates):
            raise Rejected("the batch repeats a candidate")

    return [batch_size, within_bounds, distinct]
# --8<-- [end:planner-checks]


def round_candidates(
    candidates: list[float], lower_bound: float, upper_bound: float
) -> list[float]:
    """Round to 6 decimals without moving an in-bounds value out of bounds."""
    return [_round_within(value, lower_bound, upper_bound) for value in candidates]


def fallback_plan(
    surrogate_candidates: list[float],
    *,
    expected_count: int,
    lower_bound: float,
    upper_bound: float,
) -> PlannerDecision:
    """Plan the surrogate's batch, or an evenly spaced one if it fails the checks.

    Raises ``ValueError`` when the evenly spaced batch fails them too, so that no
    unchecked batch runs.
    """
    bounds = {
        "expected_count": expected_count,
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
    }
    candidates = round_candidates(
        surrogate_candidates[:expected_count], lower_bound, upper_bound
    )
    if not _candidate_batch_is_valid(candidates, **bounds):
        candidates = evenly_spaced(lower_bound, upper_bound, expected_count)
        if not _candidate_batch_is_valid(candidates, **bounds):
            raise ValueError(
                f"[{lower_bound}, {upper_bound}] cannot hold {expected_count} "
                "distinct candidates at six decimals"
            )
    return PlannerDecision(
        candidates=candidates,
        rationale="No agent proposal passed the checks; run the surrogate's batch.",
    )


def rejected_proposal(
    decision: PlannerDecision | None, rejected_by: str, reason: str
) -> dict[str, Any]:
    """Record a rejected planner proposal, for the re-ask and the agent trace."""
    return {
        "decision": None if decision is None else decision.model_dump(),
        "rejected_by": rejected_by,
        "reason": reason,
    }


def planner_trace_entry(
    *,
    cycle: int,
    decision: PlannerDecision,
    disposition: str,
    rejected: list[dict[str, Any]],
) -> dict[str, Any]:
    """Record the planner's decision, how it was reached, and what was rejected."""
    return agent_trace_entry(
        agent="planner",
        cycle=cycle,
        decision={
            **decision.model_dump(),
            "accepted_candidates": decision.candidates,
            "disposition": disposition,
            "rejected_attempts": rejected,
        },
        policy_status=PLANNER_POLICY_STATUS[disposition],
    )


def enforce_candidate_policy(
    decision: PlannerDecision,
    fallback_candidates: list[float],
    *,
    expected_count: int,
    lower_bound: float,
    upper_bound: float,
) -> tuple[list[float], str]:
    """Validate an agent proposal and fall back to the surrogate if necessary."""
    candidates = round_candidates(decision.candidates, lower_bound, upper_bound)
    if _candidate_batch_is_valid(
        candidates,
        expected_count=expected_count,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    ):
        return candidates, PLANNER_POLICY_STATUS["accepted"]

    fallback = fallback_plan(
        fallback_candidates,
        expected_count=expected_count,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    )
    return fallback.candidates, PLANNER_POLICY_STATUS["fallback"]


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
        points = [(lower + upper) / 2]
    else:
        points = [
            lower + (upper - lower) * index / (count - 1) for index in range(count)
        ]
    return [min(max(round(point, 6), lower), upper) for point in points]


def _round_within(value: float, lower_bound: float, upper_bound: float) -> float:
    """Round to 6 decimals without moving an in-bounds value out of bounds."""
    rounded = round(float(value), 6)
    if not lower_bound <= value <= upper_bound:
        return rounded
    return min(max(rounded, lower_bound), upper_bound)


def _candidate_batch_is_valid(
    candidates: list[float],
    *,
    expected_count: int,
    lower_bound: float,
    upper_bound: float,
) -> bool:
    batch = PlannerDecision.model_construct(candidates=candidates, rationale="")
    checks = planner_checks(
        expected_count=expected_count,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
    )
    try:
        for check in checks:
            check(batch)
    except Rejected:
        return False
    return True


def _shorten(text: str, limit: int = 72) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"
