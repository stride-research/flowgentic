"""The spans Flowgentic records and the trace context it carries into tasks."""

# ruff: noqa: S101

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, StatusCode
from radical.asyncflow import LocalExecutionBackend, WorkflowEngine

from flowgentic import _asyncflow
from flowgentic.agent import bind_agent
from flowgentic.langGraph import utils as langgraph_utils
from flowgentic.langGraph.main import LangraphIntegration


class Component:
    async def ainvoke(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"choice": context["candidate"]}


class FailingComponent:
    async def ainvoke(self, context: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("model unavailable")


@pytest.fixture
async def ai_flow() -> AsyncIterator[tuple[WorkflowEngine, LocalExecutionBackend]]:
    ai = await LocalExecutionBackend(ThreadPoolExecutor(max_workers=1), name="ai")
    flow = await WorkflowEngine.create(backend=[ai])
    try:
        yield flow, ai
    finally:
        await flow.shutdown()


def _spans_named(exporter: InMemorySpanExporter, name: str) -> list[Any]:
    return [span for span in exporter.get_finished_spans() if span.name == name]


async def test_a_bound_decision_is_an_invoke_agent_span(
    ai_flow: tuple[WorkflowEngine, LocalExecutionBackend],
    span_exporter: InMemorySpanExporter,
) -> None:
    flow, ai = ai_flow
    agent = bind_agent(
        flow, Component(), identity="planner", framework="test", backend="ai"
    )

    await agent.ainvoke({"candidate": 1.0})

    (span,) = _spans_named(span_exporter, "invoke_agent planner")
    assert span.kind is SpanKind.INTERNAL
    assert dict(span.attributes) == {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "planner",
        "flowgentic.task.uid": next(iter(ai.tasks)),
        "flowgentic.task.backend": "ai",
    }


async def test_a_failed_decision_marks_its_span(
    ai_flow: tuple[WorkflowEngine, LocalExecutionBackend],
    span_exporter: InMemorySpanExporter,
) -> None:
    flow, _ = ai_flow
    agent = bind_agent(
        flow, FailingComponent(), identity="analyst", framework="test", backend="ai"
    )

    with pytest.raises(RuntimeError, match="model unavailable"):
        await agent.ainvoke({})

    (span,) = _spans_named(span_exporter, "invoke_agent analyst")
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes["error.type"] == "RuntimeError"


@pytest.mark.parametrize("executor", [ThreadPoolExecutor, ProcessPoolExecutor])
async def test_a_task_runs_in_its_callers_trace_context(
    executor: type[Executor], span_exporter: InMemorySpanExporter
) -> None:
    # Defined here, so that a process pool receives it by value
    async def current_span_ids() -> tuple[int, int]:
        context = trace.get_current_span().get_span_context()
        return context.trace_id, context.span_id

    backend = await LocalExecutionBackend(executor(max_workers=1))
    flow = await WorkflowEngine.create(backend=backend)
    try:
        submit = _asyncflow.task(flow, current_span_ids)
        with trace.get_tracer(__name__).start_as_current_span("caller") as caller:
            inside = await submit()
    finally:
        await flow.shutdown()

    expected = caller.get_span_context()
    assert inside == (expected.trace_id, expected.span_id)


def test_the_node_report_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("flowgentic.utils.telemetry")
    assert not hasattr(LangraphIntegration, "generate_execution_artifacts")
    assert not hasattr(langgraph_utils, "LangraphUtils")
