"""Unit coverage for framework-neutral agent components."""

# ruff: noqa: S101

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from flowgentic.agent import bind_agent


class FakeFlow:
    """Small AsyncFlow stand-in that records placement."""

    def __init__(self) -> None:
        self.backend: str | None = None

    def function_task(
        self,
        function: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        *,
        backend: str,
    ) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
        self.backend = backend

        def submit(context: dict[str, Any]) -> asyncio.Task[dict[str, Any]]:
            return asyncio.create_task(function(context))

        return submit


class FakeAgentComponent:
    async def ainvoke(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"choice": context["candidate"], "rationale": "bounded"}


async def test_bind_agent_places_and_identifies_framework_component() -> None:
    flow = FakeFlow()
    agent = bind_agent(
        flow,  # type: ignore[arg-type]
        FakeAgentComponent(),
        identity="planner",
        framework="test-framework",
        backend="ai",
    )

    result = await agent.ainvoke({"cycle": 3, "candidate": 1.25})

    assert flow.backend == "ai"
    assert result["agent"] == "planner"
    assert result["framework"] == "test-framework"
    assert result["decision"] == {"choice": 1.25, "rationale": "bounded"}
    assert result["event"]["name"] == "agent.planner"
    assert result["event"]["cycle"] == 3
    assert result["event"]["backend"] == "ai"
