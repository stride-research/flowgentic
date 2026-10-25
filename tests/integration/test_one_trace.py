"""One trace across a decision, the task it runs in and its model call.

A decision bound with ``bind_agent`` calls a scripted OpenAI-compatible model
inside an AsyncFlow task. OpenTelemetry's LangChain instrumentation records the
model call, RHAPSODY records the task, and Flowgentic records the decision.
"""

# ruff: noqa: S101

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from langchain_openai import ChatOpenAI
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from pydantic import BaseModel
from radical.asyncflow import LocalExecutionBackend, WorkflowEngine

from flowgentic.agent import bind_agent

pytestmark = pytest.mark.usefixtures("genai_instrumentation")

# OpenRouter-shaped usage: 194 input tokens, 64 of them read from the cache.
USAGE = {
    "prompt_tokens": 194,
    "completion_tokens": 12,
    "total_tokens": 206,
    "prompt_tokens_details": {"cached_tokens": 64},
    "completion_tokens_details": {"reasoning_tokens": 5},
}
PLAN = json.dumps({"candidates": [0.1, 0.5]})


class Plan(BaseModel):
    candidates: list[float]


class Planner:
    def __init__(self, model: ChatOpenAI) -> None:
        self.model = model

    async def ainvoke(self, context: dict[str, Any]) -> Plan:
        return await self.model.with_structured_output(Plan).ainvoke("plan")


RunDecision = Callable[[dict[str, Any] | None], Any]


@pytest.fixture
def run_decision(
    span_exporter: InMemorySpanExporter,
    scripted_chat_model: Callable[..., ChatOpenAI],
) -> RunDecision:
    """Run one bound decision with RHAPSODY's telemetry on; return all spans."""

    async def run(usage: dict[str, Any] | None) -> list[ReadableSpan]:
        # RHAPSODY's stop() shuts down the processors it was given, so its
        # spans go to an exporter of their own
        rhapsody_exporter = InMemorySpanExporter()
        ai = await LocalExecutionBackend(ThreadPoolExecutor(max_workers=1), name="ai")
        flow = await WorkflowEngine.create(backend=[ai])
        telemetry = await flow.start_telemetry(
            span_processors=[SimpleSpanProcessor(rhapsody_exporter)]
        )
        try:
            agent = bind_agent(
                flow,
                Planner(scripted_chat_model(PLAN, usage=usage)),
                identity="planner",
                framework="langchain",
                backend="ai",
            )
            await agent.ainvoke({})
        finally:
            await flow.shutdown()
            await telemetry.stop()
        return [
            *span_exporter.get_finished_spans(),
            *rhapsody_exporter.get_finished_spans(),
        ]

    return run


def only(spans: list[ReadableSpan], prefix: str) -> ReadableSpan:
    (span,) = [span for span in spans if span.name.startswith(prefix)]
    return span


def ancestors(span: ReadableSpan, spans: list[ReadableSpan]) -> list[str]:
    by_id = {other.context.span_id: other for other in spans}
    names = []
    while span.parent is not None and span.parent.span_id in by_id:
        span = by_id[span.parent.span_id]
        names.append(span.name)
    return names


async def test_decision_task_and_model_call_form_one_trace(
    run_decision: RunDecision,
) -> None:
    spans = await run_decision(USAGE)

    decision = only(spans, "invoke_agent planner")
    task = only(spans, "task")
    chat = only(spans, "chat scripted-model")

    assert {span.context.trace_id for span in spans} == {decision.context.trace_id}
    assert task.parent.span_id == decision.context.span_id
    assert task.attributes["task_id"] == decision.attributes["flowgentic.task.uid"]
    assert "invoke_agent planner" in ancestors(chat, spans)
    assert {
        key: value
        for key, value in chat.attributes.items()
        if key.startswith("gen_ai.usage.")
    } == {
        "gen_ai.usage.input_tokens": 194,
        "gen_ai.usage.output_tokens": 12,
        "gen_ai.usage.cache_read.input_tokens": 64,
        "gen_ai.usage.reasoning.output_tokens": 5,
    }


async def test_a_response_without_usage_records_none(
    run_decision: RunDecision,
) -> None:
    spans = await run_decision(None)

    chat = only(spans, "chat scripted-model")
    assert not [key for key in chat.attributes if key.startswith("gen_ai.usage.")]
