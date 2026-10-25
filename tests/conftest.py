"""Fixtures shared by the unit and integration suites."""

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from langchain_openai import ChatOpenAI
from opentelemetry import trace
from opentelemetry.instrumentation.genai.langchain import LangChainInstrumentor
from opentelemetry.instrumentation.genai.openai import OpenAIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

# The global tracer provider can be set once per process, so every test that
# reads spans shares this exporter and clears it first.
_EXPORTER = InMemorySpanExporter()


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Record the spans of the global tracer provider during one test."""
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
        trace.set_tracer_provider(provider)
    _EXPORTER.clear()
    yield _EXPORTER
    _EXPORTER.clear()


@pytest.fixture(scope="module")
def genai_instrumentation() -> Iterator[None]:
    """Turn on OpenTelemetry's LangChain and OpenAI instrumentation for a module.

    The LangChain one records each model call; the OpenAI one, nested inside
    it, adds the server's address and port to the same span.
    """
    instrumentors = [LangChainInstrumentor(), OpenAIInstrumentor()]
    for instrumentor in instrumentors:
        instrumentor.instrument()
    yield
    for instrumentor in instrumentors:
        instrumentor.uninstrument()


ScriptedChatModel = Callable[..., ChatOpenAI]


@pytest.fixture
def scripted_chat_model() -> ScriptedChatModel:
    """Build chat models whose OpenAI-compatible endpoint answers from a script."""

    def build(
        content: str,
        *,
        usage: dict[str, Any] | None = None,
        base_url: str = "http://scripted.invalid/v1",
    ) -> ChatOpenAI:
        def answer(request: httpx.Request) -> httpx.Response:
            body: dict[str, Any] = {
                "id": "gen-1",
                "object": "chat.completion",
                "model": "scripted-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            }
            if usage is not None:
                body["usage"] = usage
            return httpx.Response(200, json=body)

        return ChatOpenAI(
            model="scripted-model",
            api_key="unused",
            base_url=base_url,
            http_async_client=httpx.AsyncClient(
                transport=httpx.MockTransport(answer)
            ),
        )

    return build
