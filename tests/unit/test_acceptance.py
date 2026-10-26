"""Acceptance of an agent's output: checks, re-asks, fallback and telemetry."""

# ruff: noqa: S101

from __future__ import annotations

import json
import pickle
from collections.abc import Iterable
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, StatusCode
from pydantic import BaseModel

from flowgentic.acceptance import Attempt, NotAccepted, Rejected, accept


class Batch(BaseModel):
    candidates: list[float]


class Proposer:
    """A producer that returns, or raises, the scripted items in turn."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.received: list[tuple[Attempt[Any], ...]] = []

    async def __call__(self, previous: tuple[Attempt[Any], ...]) -> Any:
        self.received.append(previous)
        item = self.script[len(self.received) - 1]
        if isinstance(item, Exception):
            raise item
        return item


def within_bounds(batch: Batch) -> None:
    outside = [x for x in batch.candidates if not -1.0 <= x <= 1.0]
    if outside:
        raise Rejected(f"{outside} lie outside [-1.0, 1.0]")


async def distinct(batch: Batch) -> None:
    if len(set(batch.candidates)) != len(batch.candidates):
        raise Rejected("the batch repeats a candidate")


VALID = Batch(candidates=[-0.5, 0.5])
OUTSIDE = Batch(candidates=[0.5, 3.0])
REPEATED = Batch(candidates=[0.5, 0.5])
SURROGATE = Batch(candidates=[-1.0, 1.0])


async def test_a_value_that_passes_every_check_is_accepted_at_once() -> None:
    propose = Proposer(VALID)

    outcome = await accept(propose, checks=[within_bounds, distinct], attempts=2)

    assert outcome.value == VALID
    assert outcome.disposition == "accepted"
    assert outcome.attempts == (Attempt(VALID),)
    assert propose.received == [()]


async def test_a_reask_receives_the_earlier_attempts() -> None:
    propose = Proposer(OUTSIDE, VALID)

    outcome = await accept(propose, checks=[within_bounds], attempts=2)

    rejected = Attempt(OUTSIDE, "within_bounds", "[3.0] lie outside [-1.0, 1.0]")
    assert outcome.value == VALID
    assert outcome.disposition == "accepted"
    assert outcome.attempts == (rejected, Attempt(VALID))
    assert propose.received == [(), (rejected,)]


async def test_exhausted_attempts_end_in_the_fallback_unchecked() -> None:
    checked: list[Batch] = []

    def record(batch: Batch) -> None:
        checked.append(batch)

    async def surrogate_batch() -> Batch:
        return Batch(candidates=[9.0])

    outcome = await accept(
        Proposer(OUTSIDE, REPEATED),
        checks=[record, within_bounds, distinct],
        attempts=2,
        fallback=surrogate_batch,
    )

    assert outcome.value == Batch(candidates=[9.0])
    assert outcome.disposition == "fallback"
    assert outcome.attempts == (
        Attempt(OUTSIDE, "within_bounds", "[3.0] lie outside [-1.0, 1.0]"),
        Attempt(REPEATED, "distinct", "the batch repeats a candidate"),
    )
    assert checked == [OUTSIDE, REPEATED]


async def test_a_sync_fallback_is_called() -> None:
    outcome = await accept(
        Proposer(OUTSIDE), checks=[within_bounds], fallback=lambda: SURROGATE
    )

    assert outcome.value == SURROGATE
    assert outcome.disposition == "fallback"


async def test_exhausted_attempts_without_a_fallback_raise_not_accepted() -> None:
    with pytest.raises(NotAccepted, match="plan") as raised:
        await accept(
            Proposer(OUTSIDE, REPEATED),
            checks=[within_bounds, distinct],
            attempts=2,
            name="plan",
        )

    assert raised.value.attempts == (
        Attempt(OUTSIDE, "within_bounds", "[3.0] lie outside [-1.0, 1.0]"),
        Attempt(REPEATED, "distinct", "the batch repeats a candidate"),
    )


async def test_a_validation_error_from_the_producer_is_a_rejection() -> None:
    async def parse(previous: tuple[Attempt[Batch], ...]) -> Batch:
        if not previous:
            return Batch.model_validate({"candidates": "not a list"})
        return VALID

    outcome = await accept(parse, attempts=2)

    rejected, accepted = outcome.attempts
    assert rejected.value is None
    assert rejected.rejected_by == "parse"
    assert "candidates" in rejected.reason
    assert accepted == Attempt(VALID)


async def test_a_producers_own_rejection_names_its_stage() -> None:
    outcome = await accept(
        Proposer(Rejected("Invalid JSON", check="schema"), VALID), attempts=2
    )

    assert outcome.attempts == (Attempt(None, "schema", "Invalid JSON"), Attempt(VALID))


async def test_any_other_exception_propagates_at_once() -> None:
    propose = Proposer(RuntimeError("model unavailable"), VALID)

    with pytest.raises(RuntimeError, match="model unavailable"):
        await accept(propose, attempts=3, fallback=lambda: SURROGATE)

    assert len(propose.received) == 1


async def test_a_failing_check_propagates_at_once() -> None:
    def broken(batch: Batch) -> None:
        raise KeyError("bound")

    propose = Proposer(VALID, VALID)

    with pytest.raises(KeyError):
        await accept(propose, checks=[broken], attempts=2, fallback=lambda: SURROGATE)

    assert len(propose.received) == 1


async def test_checks_run_in_order_and_stop_at_the_first_rejection() -> None:
    ran: list[str] = []

    def cheap(batch: Batch) -> None:
        ran.append("cheap")
        raise Rejected("too cheap to pass")

    async def expensive(batch: Batch) -> None:
        ran.append("expensive")

    outcome = await accept(
        Proposer(VALID), checks=[cheap, expensive], fallback=lambda: SURROGATE
    )

    assert ran == ["cheap"]
    assert outcome.attempts == (Attempt(VALID, "cheap", "too cheap to pass"),)


async def test_an_async_check_is_awaited() -> None:
    outcome = await accept(
        Proposer(REPEATED), checks=[distinct], fallback=lambda: SURROGATE
    )

    assert outcome.attempts == (
        Attempt(REPEATED, "distinct", "the batch repeats a candidate"),
    )


async def test_attempts_defaults_to_one_call() -> None:
    propose = Proposer(OUTSIDE, VALID)

    outcome = await accept(propose, checks=[within_bounds], fallback=lambda: SURROGATE)

    assert outcome.disposition == "fallback"
    assert len(propose.received) == 1


async def test_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError, match="attempts"):
        await accept(Proposer(VALID), attempts=0)


def test_rejections_survive_pickling() -> None:
    # A producer may run in a process pool, which pickles what it raises.
    rejected = pickle.loads(pickle.dumps(Rejected("Invalid JSON", check="schema")))
    attempts = (Attempt(None, "schema", "x"),)
    not_accepted = pickle.loads(
        pickle.dumps(NotAccepted("plan: nothing accepted", attempts))
    )

    assert (rejected.reason, rejected.check) == ("Invalid JSON", "schema")
    assert str(not_accepted) == "plan: nothing accepted"
    assert not_accepted.attempts == attempts


# Telemetry


def _spans(exporter: InMemorySpanExporter, name: str) -> list[Any]:
    return [span for span in exporter.get_finished_spans() if span.name == name]


def _evaluations(exporter: InMemoryLogRecordExporter) -> list[Any]:
    return [
        record.log_record
        for record in exporter.get_finished_logs()
        if record.log_record.event_name == "gen_ai.evaluation.result"
    ]


def _labels(records: Iterable[Any]) -> list[tuple[str, str, str | None]]:
    return [
        (
            record.attributes["gen_ai.evaluation.name"],
            record.attributes["gen_ai.evaluation.score.label"],
            record.attributes.get("gen_ai.evaluation.explanation"),
        )
        for record in records
    ]


async def test_accept_records_its_attempts_and_their_evaluations(
    span_exporter: InMemorySpanExporter,
    log_exporter: InMemoryLogRecordExporter,
) -> None:
    await accept(
        Proposer(OUTSIDE, VALID),
        checks=[within_bounds, distinct],
        attempts=3,
        name="planner",
    )

    (span,) = _spans(span_exporter, "accept planner")
    assert span.kind is SpanKind.INTERNAL
    assert span.status.status_code is StatusCode.UNSET
    assert dict(span.attributes) == {
        "flowgentic.accept.name": "planner",
        "flowgentic.accept.attempts.max": 3,
        "flowgentic.accept.attempts.used": 2,
        "flowgentic.accept.disposition": "accepted",
    }

    first, second = _spans(span_exporter, "accept.attempt planner")
    for number, attempt in enumerate((first, second), start=1):
        assert attempt.kind is SpanKind.INTERNAL
        assert attempt.parent.span_id == span.context.span_id
        assert attempt.status.status_code is StatusCode.UNSET
        assert dict(attempt.attributes) == {"flowgentic.accept.attempt": number}

    evaluations = _evaluations(log_exporter)
    assert _labels(evaluations) == [
        ("within_bounds", "fail", "[3.0] lie outside [-1.0, 1.0]"),
        ("within_bounds", "pass", None),
        ("distinct", "pass", None),
    ]
    # Each event is emitted while its attempt's span is current
    assert [record.span_id for record in evaluations] == [
        first.context.span_id,
        second.context.span_id,
        second.context.span_id,
    ]


async def test_a_producers_rejection_is_an_evaluation_of_its_stage(
    span_exporter: InMemorySpanExporter,
    log_exporter: InMemoryLogRecordExporter,
) -> None:
    await accept(
        Proposer(Rejected("Invalid JSON", check="schema")),
        checks=[within_bounds],
        fallback=lambda: SURROGATE,
    )

    assert _labels(_evaluations(log_exporter)) == [("schema", "fail", "Invalid JSON")]


async def test_the_fallback_disposition_is_recorded(
    span_exporter: InMemorySpanExporter,
) -> None:
    await accept(
        Proposer(OUTSIDE), checks=[within_bounds], fallback=lambda: SURROGATE, name="p"
    )

    (span,) = _spans(span_exporter, "accept p")
    assert span.attributes["flowgentic.accept.disposition"] == "fallback"
    assert span.attributes["flowgentic.accept.attempts.used"] == 1
    assert span.status.status_code is StatusCode.UNSET


async def test_not_accepted_marks_the_accept_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    with pytest.raises(NotAccepted):
        await accept(Proposer(OUTSIDE), checks=[within_bounds], name="p")

    (span,) = _spans(span_exporter, "accept p")
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["error.type"] == "NotAccepted"
    assert span.attributes["flowgentic.accept.disposition"] == "not_accepted"
    (attempt,) = _spans(span_exporter, "accept.attempt p")
    assert attempt.status.status_code is StatusCode.UNSET


async def test_a_failure_marks_the_attempt_and_accept_spans(
    span_exporter: InMemorySpanExporter,
) -> None:
    with pytest.raises(RuntimeError):
        await accept(Proposer(RuntimeError("model unavailable")), name="p")

    for name in ("accept p", "accept.attempt p"):
        (span,) = _spans(span_exporter, name)
        assert span.status.status_code is StatusCode.ERROR
        assert span.attributes["error.type"] == "RuntimeError"
    (span,) = _spans(span_exporter, "accept p")
    assert span.attributes["flowgentic.accept.attempts.used"] == 1
    assert "flowgentic.accept.disposition" not in span.attributes


async def test_rejected_values_are_recorded_only_with_content_capture(
    span_exporter: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> list[Any]:
        span_exporter.clear()
        await accept(Proposer(OUTSIDE, VALID), checks=[within_bounds], attempts=2)
        return _spans(span_exporter, "accept.attempt Proposer")

    monkeypatch.delenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", False)
    assert all("flowgentic.accept.value" not in s.attributes for s in await run())

    for mode in ("EVENT_ONLY", "NO_CONTENT"):
        monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", mode)
        assert all("flowgentic.accept.value" not in s.attributes for s in await run())

    for mode in ("SPAN_ONLY", "span_and_event"):
        monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", mode)
        rejected, accepted = await run()
        value = json.loads(rejected.attributes["flowgentic.accept.value"])
        assert value == {"candidates": [0.5, 3.0]}
        assert "flowgentic.accept.value" not in accepted.attributes


async def test_a_messages_response_id_is_on_its_evaluations(
    log_exporter: InMemoryLogRecordExporter,
) -> None:
    message = AIMessage("[0.5]", response_metadata={"id": "chatcmpl-42"})

    def non_empty(reply: AIMessage) -> None:
        if not reply.content:
            raise Rejected("empty reply")

    await accept(Proposer(message), checks=[non_empty])

    (evaluation,) = _evaluations(log_exporter)
    assert evaluation.attributes["gen_ai.response.id"] == "chatcmpl-42"
