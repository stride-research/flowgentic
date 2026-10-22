"""Offline coverage for the chat-model providers used by live demo runs."""

# ruff: noqa: S101, S106

from __future__ import annotations

import pytest
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from flowgentic.utils.llm_providers import ChatLLMProvider, ChatOpenRouter


def test_openrouter_provider_targets_openrouter() -> None:
    model = ChatLLMProvider(
        provider="OpenRouter",
        model="google/gemini-2.5-flash",
        api_key="test-key",
        temperature=0.4,
    )

    assert isinstance(model, ChatOpenRouter)
    assert model.openai_api_base == "https://openrouter.ai/api/v1"
    assert model.openai_api_key is not None
    assert model.openai_api_key.get_secret_value() == "test-key"
    assert model.temperature == 0.4


def test_openrouter_provider_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPEN_ROUTER_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OpenRouter API key"):
        ChatLLMProvider(provider="openrouter", model="google/gemini-2.5-flash")


def test_openai_and_ollama_providers_build_offline() -> None:
    openai = ChatLLMProvider(provider="chatopenai", model="gpt-4o", api_key="test-key")
    ollama = ChatLLMProvider(provider="ollama", model="llama3", temperature=0.0)

    assert isinstance(openai, ChatOpenAI)
    assert isinstance(ollama, ChatOllama)
    assert ollama.model == "llama3"
