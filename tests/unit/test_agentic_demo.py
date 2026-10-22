"""Unit tests for the bounded decision layer in the agentic AI-HPC demo."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

DEMO_DIR = Path(__file__).resolve().parents[2] / "examples" / "ai-hpc-coupling"
sys.path.insert(0, str(DEMO_DIR))

from agentic_support import (  # noqa: E402
    PlannerDecision,
    RehearsalDecisionModel,
    SupervisorDecision,
    enforce_candidate_policy,
    enforce_supervisor_policy,
)


def test_valid_agent_candidates_are_accepted() -> None:
    decision = PlannerDecision(
        candidates=[-1.0, 0.0, 1.0],
        rationale="Explore the bounded region.",
    )

    candidates, status = enforce_candidate_policy(
        decision,
        fallback_candidates=[-2.0, 0.0, 2.0],
        expected_count=3,
        lower_bound=-2.0,
        upper_bound=2.0,
    )

    assert candidates == [-1.0, 0.0, 1.0]
    assert status == "agent proposal accepted"


@pytest.mark.parametrize(
    "candidates",
    (
        [-3.0, 0.0, 1.0],
        [-1.0, 1.0],
        [0.0, 0.0, 1.0],
    ),
)
def test_invalid_agent_candidates_use_surrogate_fallback(
    candidates: list[float],
) -> None:
    decision = PlannerDecision(
        candidates=candidates,
        rationale="An invalid proposal used to exercise the guard.",
    )

    accepted, status = enforce_candidate_policy(
        decision,
        fallback_candidates=[-2.0, 0.0, 2.0],
        expected_count=3,
        lower_bound=-2.0,
        upper_bound=2.0,
    )

    assert accepted == [-2.0, 0.0, 2.0]
    assert status == "agent proposal rejected; surrogate fallback enforced"


def test_invalid_surrogate_fallback_is_rebounded() -> None:
    decision = PlannerDecision(
        candidates=[-3.0, 0.0, 3.0],
        rationale="An out-of-bounds agent proposal.",
    )

    accepted, status = enforce_candidate_policy(
        decision,
        fallback_candidates=[-9.0, 0.0, 9.0],
        expected_count=3,
        lower_bound=-2.0,
        upper_bound=2.0,
    )

    assert accepted == [-2.0, 0.0, 2.0]
    assert status == "agent proposal rejected; surrogate fallback enforced"


def test_agent_cannot_stop_before_a_hard_condition() -> None:
    decision = SupervisorDecision(
        recommendation="stop",
        next_strategy="exploit",
        rationale="The agent would prefer to stop.",
    )

    recommendation, status = enforce_supervisor_policy(
        decision,
        uncertainty=0.8,
        uncertainty_threshold=0.25,
        spent=8,
        budget=24,
    )

    assert recommendation == "continue"
    assert status == "premature agent stop rejected by hard policy"


def test_hard_condition_overrides_agent_continue_recommendation() -> None:
    decision = SupervisorDecision(
        recommendation="continue",
        next_strategy="explore",
        rationale="The agent would prefer another cycle.",
    )

    recommendation, status = enforce_supervisor_policy(
        decision,
        uncertainty=0.2,
        uncertainty_threshold=0.25,
        spent=12,
        budget=24,
    )

    assert recommendation == "stop"
    assert status == "hard stop overrides supervisor"


async def test_rehearsal_agents_return_structured_bounded_decisions() -> None:
    agents = RehearsalDecisionModel()
    decision = await agents.plan(
        {
            "expected_count": 4,
            "lower_bound": -4.0,
            "upper_bound": 4.0,
            "strategy": "balanced",
            "best_x": None,
            "surrogate_candidates": [-4.0, -1.333333, 1.333333, 4.0],
        }
    )

    assert decision.candidates == [-4.0, -1.333333, 1.333333, 4.0]
    assert decision.rationale
