"""Run the design-pattern examples offline, with a scripted chat model.

The examples build their models with ``ChatLLMProvider``, which needs a model
provider and credentials, so CI could not run them. Here each example runs from
a copy in ``tmp_path``, and the modules that import ``ChatLLMProvider`` get a
factory that returns a scripted chat model instead. The live runs stay in
``test_integration.py``, opt-in.
"""

# ruff: noqa: S101

from __future__ import annotations

import importlib
import shutil
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

from flowgentic.langGraph.main import LangraphIntegration

DESIGN_PATTERNS = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "langgraph-integration"
    / "design_patterns"
)
ANSWER = "Scripted answer."


class ScriptedChatModel(BaseChatModel):
    """A chat model that replies from a script, with no provider.

    With tools bound, it first calls every one of them, with the arguments the
    script gives for each, and answers once their results are back. Structured
    output binds its schema as a tool, so the script's arguments for the schema
    are the parsed object. An answer is the reply for the first phrase in
    ``replies`` that the last message contains, or ``ANSWER``.

    LangChain's ``GenericFakeChatModel`` replies from a queue and binds no
    tools, while the examples' agents bind tools and the supervisors' workers
    run in parallel, so this model replies by rule instead.
    """

    tool_args: dict[str, dict[str, Any]] = {}
    replies: dict[str, str] = {}

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
        return self.bind(tools=[convert_to_openai_tool(tool) for tool in tools])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        tools: Sequence[dict[str, Any]] = (),
        **kwargs: Any,
    ) -> ChatResult:
        last = messages[-1]
        if tools and not isinstance(last, ToolMessage):
            names = [tool["function"]["name"] for tool in tools]
            reply = AIMessage(
                content="",
                tool_calls=[
                    {"name": name, "args": self.tool_args[name], "id": f"call_{name}"}
                    for name in names
                ],
            )
        else:
            answer = next(
                (text for phrase, text in self.replies.items() if phrase in last.text),
                ANSWER,
            )
            reply = AIMessage(content=answer)
        return ChatResult(generations=[ChatGeneration(message=reply)])


def scripted_provider(**script: Any) -> Callable[..., ScriptedChatModel]:
    """Return a stand-in for ``ChatLLMProvider`` that builds scripted models."""

    def provider(provider: str, *args: Any, **kwargs: Any) -> ScriptedChatModel:
        return ScriptedChatModel(**script)

    return provider


async def run_example(
    example: str,
    entry_point: str,
    model_modules: list[str],
    provider: Callable[..., ScriptedChatModel],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Path]:
    """Run a copy of an example with ``provider`` in place of ``ChatLLMProvider``.

    Returns the final state that the example hands to
    ``generate_execution_artifacts``, and the copy's directory.
    """
    package = "offline_" + example.replace("/", "_")
    copy = tmp_path / package
    # Without the committed results, a report in the copy is this run's
    shutil.copytree(
        DESIGN_PATTERNS / example,
        copy,
        ignore=shutil.ignore_patterns("agent_execution_results", "__pycache__"),
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    # A module left unpatched fails for want of a key, not with a live call
    monkeypatch.delenv("OPEN_ROUTER_API_KEY", raising=False)

    final_states = []
    generate_execution_artifacts = LangraphIntegration.generate_execution_artifacts

    async def record_final_state(
        self: LangraphIntegration,
        app: Any,
        caller_file_path: str,
        final_state: Any,
        **kwargs: Any,
    ) -> None:
        final_states.append(final_state)
        await generate_execution_artifacts(
            self, app, caller_file_path, final_state, **kwargs
        )

    monkeypatch.setattr(
        LangraphIntegration, "generate_execution_artifacts", record_final_state
    )

    try:
        for module in model_modules:
            monkeypatch.setattr(
                importlib.import_module(f"{package}.{module}"),
                "ChatLLMProvider",
                provider,
            )
        main = importlib.import_module(f"{package}.main")
        await getattr(main, entry_point)()
    finally:
        for name in list(sys.modules):
            if name == package or name.startswith(f"{package}."):
                del sys.modules[name]

    assert len(final_states) == 1
    return final_states[0], copy


def tool_results(state: Any) -> dict[str, str]:
    """Map each tool that ran to its result's status."""
    return {
        message.name: message.status
        for message in state["messages"]
        if isinstance(message, ToolMessage)
    }


def report(copy: Path) -> Path:
    return copy / "agent_execution_results" / "execution_summary.md"


async def test_sequential_research_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = scripted_provider(
        tool_args={
            "web_search_tool": {"query": "renewable energy storage"},
            "data_analysis_tool": {"data": "battery storage costs"},
            "document_generator_tool": {"content": {"key_points": ["storage"]}},
        }
    )

    state, copy = await run_example(
        "sequential/research_agent",
        "start_app",
        ["components.nodes"],
        provider,
        tmp_path,
        monkeypatch,
    )

    assert state["current_stage"] == "completed", state["errors"]
    assert state["errors"] == []
    assert state["research_agent_output"].output_content == ANSWER
    assert state["synthesis_agent_output"].output_content == ANSWER
    assert tool_results(state) == {
        "web_search_tool": "success",
        "data_analysis_tool": "success",
        "document_generator_tool": "success",
    }
    assert report(copy).is_file()


async def test_sequential_research_agent_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = scripted_provider(
        tool_args={
            "web_search": {"query": "renewable energy storage"},
            "data_analysis": {"data": "battery storage costs"},
            "document_generator": {"content": "storage findings"},
            "recommendation_engine": {"analysis": "storage findings"},
        }
    )

    state, copy = await run_example(
        "sequential/research_agent_memory",
        "start_app",
        ["main", "components.nodes"],
        provider,
        tmp_path,
        monkeypatch,
    )

    assert state["current_stage"] == "completed", state["errors"]
    assert state["errors"] == []
    assert state["research_agent_output"].output_content == ANSWER
    assert state["synthesis_agent_output"].output_content == ANSWER
    assert tool_results(state) == {
        "web_search": "success",
        "data_analysis": "success",
        "document_generator": "success",
        "recommendation_engine": "success",
    }
    assert report(copy).is_file()


async def test_supervisor_toy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = scripted_provider(
        tool_args={
            "RoutingDecision": {
                "routing_decision": ["agent_A", "agent_B"],
                "routing_rationale": "The query asks for analysis and an answer.",
            }
        }
    )

    state, copy = await run_example(
        "supervisor/toy", "main", ["main"], provider, tmp_path, monkeypatch
    )

    assert state["routing_decision"] == ["agent_A", "agent_B"]
    assert set(state["results"]) == {"agent_A", "agent_B"}
    assert state["final_summary"] == ANSWER
    assert report(copy).is_file()


async def test_supervisor_toy_raises_the_graph_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = scripted_provider(
        tool_args={
            "RoutingDecision": {
                "routing_decision": [],
                "routing_rationale": "No agent fits the query.",
            }
        }
    )

    with pytest.raises(ValueError, match="Empty routing decision"):
        await run_example(
            "supervisor/toy", "main", ["main"], provider, tmp_path, monkeypatch
        )


async def test_supervisor_product_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = scripted_provider(
        tool_args={
            "RoutingDecision": {
                "routing_decision": ["technical_specs_agent", "user_reviews_agent"],
                "routing_rationale": "The query asks for specs and reviews.",
            },
            "search_product_specifications": {"product_name": "iPhone 15 Pro"},
            "search_user_reviews": {"product_name": "iPhone 15 Pro"},
        },
        replies={"Audience type: technical": "technical_synthesizer"},
    )

    state, copy = await run_example(
        "supervisor/product_research", "main", ["main"], provider, tmp_path, monkeypatch
    )

    assert state["routing_decision"] == ["technical_specs_agent", "user_reviews_agent"]
    assert set(state["results"]) == {"technical_specs", "user_reviews"}
    assert state["audience_type"] == "technical"
    assert state["synthesis_decision"] == "technical_synthesizer"
    assert state["final_report"] == ANSWER
    assert tool_results(state) == {
        "search_product_specifications": "success",
        "search_user_reviews": "success",
    }
    assert report(copy).is_file()
