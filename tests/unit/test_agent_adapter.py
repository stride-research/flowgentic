"""Unit coverage for framework-neutral agent components."""

# ruff: noqa: S101

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from pydantic import BaseModel
from radical.asyncflow import LocalExecutionBackend, WorkflowEngine

from flowgentic.agent import bind_agent


class FakeFlow:
    """Small AsyncFlow stand-in that records placement."""

    def __init__(self) -> None:
        self.backend: str | None = None
        self.function: Callable[..., Any] | None = None

    def function_task(
        self,
        function: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        *,
        backend: str,
        capture_stdio: bool = False,
    ) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
        self.backend = backend
        self.function = function

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


class PlannerChoice(BaseModel):
    choice: float
    rationale: str


class StructuredAgentComponent:
    async def ainvoke(self, context: dict[str, Any]) -> PlannerChoice:
        return PlannerChoice(choice=context["candidate"], rationale="structured")


class SlowAgentComponent:
    async def ainvoke(self, context: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0.05)
        return {"choice": 0.0}


class UnstructuredAgentComponent:
    async def ainvoke(self, context: dict[str, Any]) -> str:
        return "free text"


async def test_bind_agent_normalizes_pydantic_structured_output() -> None:
    agent = bind_agent(
        FakeFlow(),  # type: ignore[arg-type]
        StructuredAgentComponent(),
        identity="planner",
        framework="pydantic",
    )

    result = await agent.ainvoke({"cycle": 1, "candidate": 0.5})

    assert result["decision"] == {"choice": 0.5, "rationale": "structured"}


async def test_bind_agent_rejects_unstructured_output() -> None:
    agent = bind_agent(
        FakeFlow(),  # type: ignore[arg-type]
        UnstructuredAgentComponent(),
        identity="planner",
        framework="test-framework",
    )

    with pytest.raises(TypeError, match="mapping or Pydantic model"):
        await agent.ainvoke({"cycle": 1})


def test_bind_agent_exposes_a_stable_identity() -> None:
    flow = FakeFlow()
    agent = bind_agent(
        flow,  # type: ignore[arg-type]
        FakeAgentComponent(),
        identity="lead-planner",
        framework="test-framework",
        backend="gpu",
    )

    assert (agent.identity, agent.framework, agent.backend) == (
        "lead-planner",
        "test-framework",
        "gpu",
    )
    assert flow.backend == "gpu"
    assert flow.function is not None
    assert flow.function.__name__ == "agent_lead_planner"


async def test_bind_agent_times_the_component_call() -> None:
    agent = bind_agent(
        FakeFlow(),  # type: ignore[arg-type]
        SlowAgentComponent(),
        identity="analyst",
        framework="test-framework",
    )

    event = (await agent.ainvoke({}))["event"]

    assert event["cycle"] == -1
    assert event["finished"] >= event["started"]
    assert event["duration"] == pytest.approx(event["finished"] - event["started"])
    assert event["duration"] >= 0.04


# --8<-- [start:bind-agent-on-backend]
async def test_bind_agent_runs_on_the_named_asyncflow_backend() -> None:
    compute = await LocalExecutionBackend(
        ThreadPoolExecutor(max_workers=1), name="compute"
    )
    ai = await LocalExecutionBackend(ThreadPoolExecutor(max_workers=1), name="ai")
    flow = await WorkflowEngine.create(backend=[compute, ai])
    try:
        agent = bind_agent(
            flow,
            FakeAgentComponent(),
            identity="lead-planner",
            framework="test-framework",
            backend="ai",
        )

        result = await agent.ainvoke({"cycle": 2, "candidate": 3.0})

        assert result["decision"] == {"choice": 3.0, "rationale": "bounded"}
        assert [task["name"] for task in ai.tasks.values()] == ["agent_lead_planner"]
        assert not compute.tasks
    finally:
        await flow.shutdown()
# --8<-- [end:bind-agent-on-backend]
