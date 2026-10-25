"""Unit tests for the bounded decision layer in the agentic AI-HPC demo."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from flowgentic.acceptance import Rejected

DEMO_DIR = Path(__file__).resolve().parents[2] / "examples" / "ai-hpc-coupling"
sys.path.insert(0, str(DEMO_DIR))

from agentic_support import (  # noqa: E402
    LangChainDecisionModel,
    PlannerDecision,
    RehearsalDecisionModel,
    SupervisorDecision,
    enforce_candidate_policy,
    enforce_supervisor_policy,
    planner_checks,
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


# Bounds whose seventh decimal makes six-decimal rounding step outside them.
EDGE_LOWER = -1.2345677
EDGE_UPPER = 2.3456787


def test_agent_proposal_within_rounding_of_a_bound_is_accepted() -> None:
    decision = PlannerDecision(
        candidates=[EDGE_LOWER, 0.5, EDGE_UPPER],
        rationale="Sample both ends of the trust region.",
    )

    accepted, status = enforce_candidate_policy(
        decision,
        fallback_candidates=[-1.0, 0.0, 1.0],
        expected_count=3,
        lower_bound=EDGE_LOWER,
        upper_bound=EDGE_UPPER,
    )

    assert status == "agent proposal accepted"
    assert accepted == pytest.approx([EDGE_LOWER, 0.5, EDGE_UPPER], abs=1e-6)


@pytest.mark.parametrize(
    ("proposal", "fallback"),
    (
        pytest.param(
            [-9.0, 0.0, 9.0], [EDGE_LOWER, 0.5, EDGE_UPPER], id="surrogate fallback"
        ),
        pytest.param([-9.0, 0.0, 9.0], [-9.0, 0.0, 9.0], id="last resort"),
    ),
)
def test_every_returned_candidate_lies_within_the_bounds(
    proposal: list[float],
    fallback: list[float],
) -> None:
    decision = PlannerDecision(
        candidates=proposal,
        rationale="A proposal used to exercise the bound guard.",
    )

    accepted, _ = enforce_candidate_policy(
        decision,
        fallback_candidates=fallback,
        expected_count=3,
        lower_bound=EDGE_LOWER,
        upper_bound=EDGE_UPPER,
    )

    assert len(accepted) == 3
    assert all(EDGE_LOWER <= value <= EDGE_UPPER for value in accepted), accepted


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


async def test_langchain_planner_returns_the_parsed_decision(
    scripted_chat_model: Callable[..., ChatOpenAI],
) -> None:
    plan = {"candidates": [-1.0, 1.0], "rationale": "Spread the batch."}
    agents = LangChainDecisionModel(scripted_chat_model(json.dumps(plan)), "scripted")

    decision = await agents.plan({"expected_count": 2})

    assert decision == PlannerDecision(**plan)


async def test_langchain_planner_rejects_output_it_cannot_parse(
    scripted_chat_model: Callable[..., ChatOpenAI],
) -> None:
    agents = LangChainDecisionModel(scripted_chat_model("not a decision"), "scripted")

    # OpenAI's structured outputs raise the error inside the call
    with pytest.raises(Rejected, match="Invalid JSON") as raised:
        await agents.plan({"expected_count": 2})

    assert raised.value.check == "schema"


async def test_langchain_planner_rejects_a_refusal(
    scripted_chat_model: Callable[..., ChatOpenAI],
) -> None:
    model = scripted_chat_model(None, refusal="I cannot help with that.")
    agents = LangChainDecisionModel(model, "scripted")

    with pytest.raises(Rejected, match="I cannot help with that.") as raised:
        await agents.plan({"expected_count": 2})

    assert raised.value.check == "refusal"


async def test_langchain_planner_rejects_a_truncated_answer(
    scripted_chat_model: Callable[..., ChatOpenAI],
) -> None:
    truncated = '{"candidates": [-1.0, 1.0], "rationale": "Spread'
    model = scripted_chat_model(truncated, finish_reason="length")
    agents = LangChainDecisionModel(model, "scripted")

    with pytest.raises(Rejected, match="token limit") as raised:
        await agents.plan({"expected_count": 2})

    assert raised.value.check == "truncation"


async def test_langchain_planner_rejects_a_truncated_ollama_answer() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        chunk = {
            "model": "scripted-model",
            "created_at": "2026-10-25T00:00:00Z",
            "message": {"role": "assistant", "content": '{"candidates": [-1.0'},
            "done": True,
            "done_reason": "length",
        }
        return httpx.Response(200, content=json.dumps(chunk).encode() + b"\n")

    model = ChatOllama(
        model="scripted-model",
        base_url="http://scripted.invalid",
        async_client_kwargs={"transport": httpx.MockTransport(answer)},
    )
    agents = LangChainDecisionModel(model, "scripted")

    with pytest.raises(Rejected, match="token limit") as raised:
        await agents.plan({"expected_count": 2})

    assert raised.value.check == "truncation"


async def test_a_reask_shows_the_planner_its_rejected_answer_and_why(
    scripted_chat_model: Callable[..., ChatOpenAI],
) -> None:
    plan = {"candidates": [-1.0, 1.0], "rationale": "Spread the batch."}
    requests: list[httpx.Request] = []
    model = scripted_chat_model(json.dumps(plan), requests=requests)
    agents = LangChainDecisionModel(model, "scripted")
    rejected = {
        "decision": {"candidates": [-1.0, 9.0], "rationale": "Go wide."},
        "rejected_by": "within_bounds",
        "reason": "[9.0] lie outside [-2.0, 2.0]",
    }

    await agents.plan({"expected_count": 2, "rejected_proposals": [rejected]})

    messages = json.loads(requests[0].content)["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert "rejected_proposals" not in messages[1]["content"]
    assert json.loads(messages[2]["content"]) == rejected["decision"]
    assert "within_bounds" in messages[3]["content"]
    assert "[9.0] lie outside [-2.0, 2.0]" in messages[3]["content"]


@pytest.mark.parametrize(
    ("candidates", "check", "reason"),
    (
        ([-1.0, 1.0], "batch_size", "2 candidates; the batch takes exactly 3"),
        ([-3.0, 0.0, 1.0], "within_bounds", "[-3.0] lie outside [-2.0, 2.0]"),
        ([0.0, 0.0, 1.0], "distinct", "the batch repeats a candidate"),
    ),
)
def test_each_planner_check_names_what_it_rejects(
    candidates: list[float], check: str, reason: str
) -> None:
    decision = PlannerDecision(candidates=candidates, rationale="A test proposal.")
    checks = planner_checks(expected_count=3, lower_bound=-2.0, upper_bound=2.0)

    rejections = []
    for planner_check in checks:
        try:
            planner_check(decision)
        except Rejected as exc:
            rejections.append((planner_check.__name__, exc.reason))

    assert rejections == [(check, reason)]
