"""The cost of a run's model calls, computed from its OpenTelemetry spans.

A recipe, not a Flowgentic API. Flowgentic records no cost: prices change
weekly, and an allocation's GPU-hours come from the batch system, not from a
trace. This reads the file that the AI-HPC demo's ``--trace`` writes, one span
or event per line, and prices each inference span in one of three ways:

- the cost the provider reported, when the span carries one;
- usage times the price in force, for a model behind a priced API;
- a share of the GPU-hours, for a model served on the allocation.

Usage is what the provider reported. A span without input and output counts
was not measured, so it gets no cost rather than a cost of zero.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INFERENCE_OPERATIONS = {"chat", "text_completion", "generate_content"}

# Recorded by an instrumentation once OpenTelemetry's cost convention exists,
# or by Flowgentic's transport until then
REPORTED_COST = ("gen_ai.usage.cost.amount", "flowgentic.usage.cost")


@dataclass(frozen=True)
class Price:
    """A model's price, in US dollars per million tokens.

    Cache reads and writes cost the input price unless they are given.
    """

    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None


def inference_spans(path: Path) -> list[dict[str, Any]]:
    """Return the inference spans in a JSON-lines file of spans and events."""
    records = [json.loads(line) for line in path.read_text().splitlines() if line]
    # A span has a context; an event, a log record, has a span_id instead
    return [
        record
        for record in records
        if "context" in record
        and record["attributes"].get("gen_ai.operation.name") in INFERENCE_OPERATIONS
    ]


def reported_cost(span: dict[str, Any]) -> float | None:
    """Return the cost the provider reported, in US dollars, if the span has it."""
    for key in REPORTED_COST:
        if key in span["attributes"]:
            return span["attributes"][key]
    return None


def measured_tokens(span: dict[str, Any]) -> tuple[int, int] | None:
    """Return the input and output tokens, or ``None`` if they were not measured."""
    attributes = span["attributes"]
    try:
        return (
            attributes["gen_ai.usage.input_tokens"],
            attributes["gen_ai.usage.output_tokens"],
        )
    except KeyError:
        return None


def priced_cost(span: dict[str, Any], price: Price) -> float | None:
    """Return the cost of a call from its usage, in US dollars.

    The input count includes cached tokens. The conventions omit a cache
    count that is zero, so a missing one is priced as uncached input.
    """
    tokens = measured_tokens(span)
    if tokens is None:
        return None
    input_tokens, output_tokens = tokens
    cache_read = span["attributes"].get("gen_ai.usage.cache_read.input_tokens", 0)
    cache_write = span["attributes"].get("gen_ai.usage.cache_write.input_tokens", 0)
    uncached = input_tokens - cache_read - cache_write
    read_price = price.input if price.cache_read is None else price.cache_read
    write_price = price.input if price.cache_write is None else price.cache_write
    dollars = (
        uncached * price.input
        + cache_read * read_price
        + cache_write * write_price
        + output_tokens * price.output
    )
    return dollars / 1_000_000


def allocation_shares(
    spans: list[dict[str, Any]], gpu_hours: dict[str, float]
) -> dict[str, float]:
    """Split each deployment's GPU-hours over its calls, by tokens processed.

    ``gpu_hours`` maps a deployment's ``server.address`` to its GPU-hours over
    the campaign, idle time included, as the batch system accounts them. The
    share of a call is those hours times its input and output tokens, divided
    by the tokens of every measured call to the same deployment. Returns
    GPU-hours by span id; calls that were not measured get no share.
    """
    tokens_by_deployment: dict[str, dict[str, int]] = defaultdict(dict)
    for span in spans:
        address = span["attributes"].get("server.address")
        tokens = measured_tokens(span)
        if address in gpu_hours and tokens is not None:
            span_id = span["context"]["span_id"]
            tokens_by_deployment[address][span_id] = sum(tokens)

    shares = {}
    for address, calls in tokens_by_deployment.items():
        total = sum(calls.values())
        for span_id, tokens in calls.items():
            shares[span_id] = gpu_hours[address] * tokens / total if total else 0.0
    return shares
