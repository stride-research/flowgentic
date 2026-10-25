"""Write a demo run's OpenTelemetry spans to a JSON-lines file.

This is the application's part of tracing: Flowgentic depends on the
OpenTelemetry API only and configures nothing. The runner sets up the SDK,
turns on OpenTelemetry's LangChain instrumentation for model, tool and graph
spans and its OpenAI instrumentation, which adds the server's address to each
model call, and gives RHAPSODY a processor for AsyncFlow's task spans. Every
span goes to one file, one JSON object per line.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.instrumentation.genai.langchain import LangChainInstrumentor
from opentelemetry.instrumentation.genai.openai import OpenAIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from radical.asyncflow import WorkflowEngine


def _one_line(span: ReadableSpan) -> str:
    return span.to_json(indent=None) + "\n"


# --8<-- [start:run-trace]
class RunTrace:
    """The tracing of one run: the application's spans and RHAPSODY's."""

    def __init__(self, path: Path, service_name: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file = path.open("w", encoding="utf-8")
        self._exporter = ConsoleSpanExporter(out=self._file, formatter=_one_line)
        self._resource = Resource.create({"service.name": service_name})
        self._provider = TracerProvider(resource=self._resource)
        self._provider.add_span_processor(SimpleSpanProcessor(self._exporter))
        trace.set_tracer_provider(self._provider)
        self._instrumentors = [LangChainInstrumentor(), OpenAIInstrumentor()]
        for instrumentor in self._instrumentors:
            instrumentor.instrument()
        self._telemetry: Any = None

    async def attach(self, flow: WorkflowEngine) -> None:
        """Send the engine's task spans to the same file.

        RHAPSODY's TelemetryManager has a provider of its own. Its ``stop()``
        shuts down the processors it was given, so it gets one of its own.
        """
        self._telemetry = await flow.start_telemetry(
            span_processors=[SimpleSpanProcessor(self._exporter)],
            resource=self._resource,
        )

    async def close(self) -> None:
        """Stop RHAPSODY's telemetry, once the engine has shut down, and flush."""
        if self._telemetry is not None:
            await self._telemetry.stop()
        for instrumentor in self._instrumentors:
            instrumentor.uninstrument()
        self._provider.shutdown()
        self._file.close()
# --8<-- [end:run-trace]


@asynccontextmanager
async def run_trace(path: Path | None, service_name: str) -> AsyncIterator[
    RunTrace | None
]:
    """Trace a run into ``path``, or do nothing when ``path`` is ``None``."""
    if path is None:
        yield None
        return
    tracing = RunTrace(path, service_name)
    try:
        yield tracing
    finally:
        await tracing.close()
