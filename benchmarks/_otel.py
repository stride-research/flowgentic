"""The OpenTelemetry SDK, configured as the telemetry page configures it, for
the benchmarks' ``--otel DIR``: metrics and spans written into ``DIR`` as
``metrics.jsonl`` and ``spans.jsonl``.

The defaults are the page's configuration at scale: spans sampled per agent
(a tenth of the agents keep their whole trace), no exemplars, and metrics
exported every 10 s. ``add_arguments`` gives a benchmark the options that
change them; ``--otel-sample 1 --otel-exemplars --otel-interval 1`` keeps
every span, as the SDK's defaults and the page's first configuration did.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

SAMPLE = 0.1
INTERVAL_S = 10.0


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--otel", type=Path, default=None, help="write OpenTelemetry's export here"
    )
    parser.add_argument(
        "--no-spans", action="store_true", help="with --otel: export metrics only"
    )
    parser.add_argument(
        "--otel-sample",
        type=float,
        default=SAMPLE,
        help=f"with --otel: the share of agents whose spans are kept ({SAMPLE})",
    )
    parser.add_argument(
        "--otel-exemplars",
        action="store_true",
        help="with --otel: keep the SDK's exemplars, off by default",
    )
    parser.add_argument(
        "--otel-interval",
        type=float,
        default=INTERVAL_S,
        help=f"with --otel: seconds between metric exports ({INTERVAL_S:g})",
    )


def configure(
    out: Path,
    spans: bool = True,
    sample: float = SAMPLE,
    exemplars: bool = False,
    interval_s: float = INTERVAL_S,
) -> Callable[[], None]:
    """Write metrics every ``interval_s``, and spans unless ``spans`` is false,
    into ``out``; return a function that flushes and closes them. ``sample``
    is the share of traces kept, each agent's span the root of its trace;
    ``exemplars`` keeps the SDK's default exemplar filter."""
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import AlwaysOffExemplarFilter, MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    out.mkdir(parents=True, exist_ok=True)
    metrics_file = (out / "metrics.jsonl").open("w")
    reader = PeriodicExportingMetricReader(
        ConsoleMetricExporter(
            out=metrics_file, formatter=lambda data: data.to_json(indent=None) + "\n"
        ),
        export_interval_millis=int(1_000 * interval_s),
    )
    meter_provider = MeterProvider(
        metric_readers=[reader],
        exemplar_filter=None if exemplars else AlwaysOffExemplarFilter(),
    )
    metrics.set_meter_provider(meter_provider)
    if not spans:

        def flush_metrics() -> None:
            meter_provider.shutdown()
            metrics_file.close()

        return flush_metrics
    spans_file = (out / "spans.jsonl").open("w")
    sampler = ParentBased(TraceIdRatioBased(sample)) if sample < 1 else None
    tracer_provider = TracerProvider(sampler=sampler)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            ConsoleSpanExporter(
                out=spans_file, formatter=lambda span: span.to_json(indent=None) + "\n"
            ),
            max_queue_size=65_536,
        )
    )
    trace.set_tracer_provider(tracer_provider)

    def flush() -> None:
        meter_provider.shutdown()
        tracer_provider.shutdown()
        metrics_file.close()
        spans_file.close()

    return flush


def from_arguments(args: argparse.Namespace) -> Callable[[], None] | None:
    """``configure`` from a benchmark's options; ``None`` without ``--otel``."""
    if args.otel is None:
        return None
    return configure(
        args.otel,
        not args.no_spans,
        args.otel_sample,
        args.otel_exemplars,
        args.otel_interval,
    )


def summary(out: Path, sampled: bool = False) -> dict:
    """What ``flowgentic.telemetry`` reads back from ``out``: its problems,
    the fleet's calls and the ``chat`` spans."""
    from flowgentic.telemetry import read_export

    export = read_export(out)
    return {
        "otel_check": export.check(sampled=sampled),
        "otel_calls": export.calls,
        "otel_chat_spans": export.chat_spans,
    }
