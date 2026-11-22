"""The OpenTelemetry SDK, configured as the telemetry page configures it, for
the benchmarks' ``--otel DIR``: metrics every second, and spans in batches,
written into ``DIR`` as ``metrics.jsonl`` and ``spans.jsonl``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


def configure(out: Path, spans: bool = True) -> Callable[[], None]:
    """Write metrics every second, and spans unless ``spans`` is false, into
    ``out``; return a function that flushes and closes them."""
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    out.mkdir(parents=True, exist_ok=True)
    metrics_file = (out / "metrics.jsonl").open("w")
    reader = PeriodicExportingMetricReader(
        ConsoleMetricExporter(
            out=metrics_file, formatter=lambda data: data.to_json(indent=None) + "\n"
        ),
        export_interval_millis=1_000,
    )
    meter_provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)
    if not spans:

        def flush_metrics() -> None:
            meter_provider.shutdown()
            metrics_file.close()

        return flush_metrics
    spans_file = (out / "spans.jsonl").open("w")
    tracer_provider = TracerProvider()
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


def summary(out: Path) -> dict:
    """What ``flowgentic.telemetry`` reads back from ``out``: its problems,
    the fleet's calls and the ``chat`` spans."""
    from flowgentic.telemetry import read_export

    export = read_export(out)
    return {
        "otel_check": export.check(),
        "otel_calls": export.calls,
        "otel_chat_spans": export.chat_spans,
    }
