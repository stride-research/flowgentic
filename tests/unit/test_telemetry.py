"""The spans and metrics Flowgentic records, and the trace context it carries
into tasks."""

# ruff: noqa: S101

from __future__ import annotations

import importlib
import json
import subprocess
import sys
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
from flowgentic.langgraph import utils as langgraph_utils
from flowgentic.langgraph.main import LangGraphIntegration


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


# Values each histogram sees in a campaign, smallest to largest, in seconds
TYPICAL_SECONDS = {
    # the event loop's lag: none to a blocked loop
    "flowgentic.host.loop.lag": ("LOOP_LAG", (0.0004, 0.003, 0.02, 0.08, 0.3, 1.5)),
    # a young generation's pause to a full collection of a large heap
    "flowgentic.host.gc.pause": ("GC_PAUSE", (0.00005, 0.0003, 0.002, 0.02, 0.2, 2.0)),
    # a lookup in the tool service to an hour-long simulation
    "flowgentic.tool.duration": ("TOOL_DURATION", (0.002, 0.05, 1, 20, 300, 3000)),
    # admitted at once to a fleet saturated for minutes
    "flowgentic.fleet.admission.wait": (
        "ADMISSION_WAIT",
        (0.0005, 0.02, 0.4, 3, 40, 200),
    ),
    # a short reply to a long reasoning one
    "flowgentic.fleet.request.duration": (
        "MODEL_SERVICE",
        (0.05, 0.3, 2, 15, 60, 200),
    ),
}


def test_histograms_in_seconds_spread_typical_values_over_buckets() -> None:
    # In a fresh interpreter, so that these values stay out of the histograms
    # other tests read. Flowgentic's instruments exist before the SDK is set,
    # as in an application that configures it after importing Flowgentic.
    code = f"""
import json
from flowgentic import _telemetry
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

reader = InMemoryMetricReader()
metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
for name, values in {TYPICAL_SECONDS!r}.values():
    for value in values:
        getattr(_telemetry, name).record(value)
points = {{
    m.name: m.data.data_points[0]
    for r in reader.get_metrics_data().resource_metrics
    for s in r.scope_metrics
    for m in s.metrics
}}
print(json.dumps({{n: list(p.bucket_counts) for n, p in points.items()}}))
"""
    run = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    counts = json.loads(run.stdout)
    for metric, (_, values) in TYPICAL_SECONDS.items():
        # Each value in a bucket of its own, none past the last bound.
        assert sum(counts[metric]) == len(values), metric
        assert max(counts[metric]) == 1 and counts[metric][-1] == 0, (
            metric,
            counts[metric],
        )


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
    assert not hasattr(LangGraphIntegration, "generate_execution_artifacts")
    assert not hasattr(langgraph_utils, "LangraphUtils")
