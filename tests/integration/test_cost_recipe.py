"""The cost recipe in ``examples/telemetry/cost.py``, on recorded spans.

Scripted OpenAI-compatible models answer with fixed usage; OpenTelemetry's
LangChain instrumentation records each call; the spans, and two events, are
written as the demo's ``--trace`` writes them, and the recipe prices them.
"""

# ruff: noqa: S101

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from langchain_openai import ChatOpenAI
from opentelemetry import _logs
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

pytestmark = pytest.mark.usefixtures("genai_instrumentation")

RECIPE = Path(__file__).resolve().parents[2] / "examples" / "telemetry" / "cost.py"
REMOTE = "http://api.example.invalid/v1"
LOCAL = "http://gpu-node-1:8000/v1"


def recipe() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cost_recipe", RECIPE)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their annotations through the module's entry
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def usage(prompt: int, completion: int, cached: int = 0) -> dict[str, object]:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": cached},
    }


@pytest.fixture
async def span_file(
    tmp_path: Path,
    span_exporter: InMemorySpanExporter,
    log_exporter: InMemoryLogRecordExporter,
    scripted_chat_model: Callable[..., ChatOpenAI],
) -> Path:
    """Record one remote call and three local ones, one of them unmeasured."""
    calls = [
        (REMOTE, usage(194, 12, cached=64)),
        (LOCAL, usage(100, 20)),
        (LOCAL, usage(50, 30)),
        (LOCAL, None),
    ]
    for base_url, reported in calls:
        model = scripted_chat_model("ok", usage=reported, base_url=base_url)
        await model.ainvoke("hi")

    # The demo's file also holds events, which are log records
    logger = _logs.get_logger("events")
    logger.emit(
        event_name="gen_ai.evaluation.result",
        attributes={"gen_ai.evaluation.name": "within_bounds"},
    )
    logger.emit(body="an event without attributes")

    records = [*span_exporter.get_finished_spans(), *log_exporter.get_finished_logs()]
    path = tmp_path / "spans.jsonl"
    path.write_text("".join(record.to_json(indent=None) + "\n" for record in records))
    return path


async def test_remote_calls_are_priced_from_their_usage(span_file: Path) -> None:
    cost = recipe()
    spans = cost.inference_spans(span_file)
    remote = [s for s in spans if s["attributes"]["server.address"] != "gpu-node-1"]
    price = cost.Price(input=1.0, output=4.0, cache_read=0.1)

    (call,) = remote
    # 130 uncached input tokens, 64 read from the cache, 12 output tokens
    assert cost.priced_cost(call, price) == pytest.approx(
        (130 * 1.0 + 64 * 0.1 + 12 * 4.0) / 1_000_000
    )
    assert cost.reported_cost(call) is None


async def test_local_calls_share_the_deployments_gpu_hours(span_file: Path) -> None:
    cost = recipe()
    spans = cost.inference_spans(span_file)
    local = [s for s in spans if s["attributes"]["server.address"] == "gpu-node-1"]

    shares = cost.allocation_shares(spans, {"gpu-node-1": 2.0})

    measured = [s for s in local if cost.measured_tokens(s) is not None]
    assert len(local) == 3 and len(measured) == 2
    # 120 and 80 of the 200 tokens the server counted; the unmeasured call
    # gets no share
    assert sorted(shares.values()) == pytest.approx([0.8, 1.2])
    assert set(shares) == {s["context"]["span_id"] for s in measured}
