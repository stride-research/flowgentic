"""Academy actions as AsyncFlow tasks, offline.

Academy's local exchange and AsyncFlow's local backend, with small agents like
the examples'. Each test names the defect it covers; the defects are listed in
the v0.4.0 plan (W2, "What is broken").
"""

# ruff: noqa: S101

from __future__ import annotations

import asyncio
import logging
import warnings
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator

import pytest
from academy.agent import Agent, action
from academy.manager import Manager
from radical.asyncflow import LocalExecutionBackend, WorkflowEngine

from flowgentic.academy import AcademyIntegration

# Lifecycle events recorded by the agents, which run in Academy's worker
# threads of this process.
EVENTS: list[str] = []


class Counter(Agent):
    count: int

    async def agent_on_startup(self) -> None:
        self.count = 0
        EVENTS.append("start")

    async def agent_on_shutdown(self) -> None:
        EVENTS.append("stop")

    @action
    async def increment(self, value: int = 1) -> None:
        self.count += value

    @action
    async def get_count(self) -> int:
        return self.count


class SlowStartCounter(Counter):
    async def agent_on_startup(self) -> None:
        await asyncio.sleep(0.2)
        await super().agent_on_startup()


class Scaler(Agent):
    def __init__(self, factor: int) -> None:
        super().__init__()
        self.factor = factor

    @action
    async def scale(self, value: int) -> int:
        return value * self.factor


@pytest.fixture(autouse=True)
def clear_events() -> None:
    EVENTS.clear()


@pytest.fixture
def remote_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registration yields to the event loop, as on any non-local exchange."""
    register_agent = Manager.register_agent

    async def register_with_round_trip(
        self: Manager, *args: object, **kwargs: object
    ) -> object:
        await asyncio.sleep(0.01)
        return await register_agent(self, *args, **kwargs)

    monkeypatch.setattr(Manager, "register_agent", register_with_round_trip)


@pytest.fixture
async def flow() -> AsyncIterator[WorkflowEngine]:
    backend = await LocalExecutionBackend(ThreadPoolExecutor())
    engine = await WorkflowEngine.create(backend=backend)
    yield engine
    await engine.shutdown()


async def test_action_returns_result_through_function_task(
    flow: WorkflowEngine,
) -> None:
    """Defects 2 and 3: a function task runs in a worker thread's event loop."""
    async with AcademyIntegration(flow) as integration:
        increment = flow.function_task(
            integration.create_agent_task(Counter, "increment")
        )
        get_count = flow.function_task(
            integration.create_agent_task(Counter, "get_count")
        )

        await increment(2)
        assert await get_count() == 2


async def test_action_returns_result_through_block(flow: WorkflowEngine) -> None:
    """Defect 2: a block runs on the engine's loop, outside the manager's context."""
    async with AcademyIntegration(flow) as integration:
        scale = flow.block(
            integration.create_agent_task(Scaler, "scale", agent_kwargs={"factor": 3})
        )

        assert await scale(2) == 6


async def test_action_returns_result_through_academy_chain(
    flow: WorkflowEngine,
) -> None:
    """Defect 2: the chain wraps each action in a block."""
    async with AcademyIntegration(flow) as integration:
        chain = integration.create_workflow_from_academy_chain(
            [
                (Scaler, "scale", (), {"factor": 2}),
                (Scaler, "scale", (), {"factor": 3}),
            ]
        )

        assert await chain(1) == 6


async def test_action_returning_none(flow: WorkflowEngine) -> None:
    """Defect 3: the result was awaited a second time."""
    async with AcademyIntegration(flow) as integration:
        increment = integration.create_agent_task(Counter, "increment")

        assert await increment(1) is None
        assert await flow.function_task(increment)(1) is None


@pytest.mark.parametrize("caller", ["manager loop", "function task"])
async def test_concurrent_first_calls_launch_one_agent(
    flow: WorkflowEngine, caller: str, remote_registration: None
) -> None:
    """Defect 4: the membership check and the launch were split by an await."""
    async with AcademyIntegration(flow) as integration:
        increment = integration.create_agent_task(
            Counter, "increment", agent_id="shared"
        )
        if caller == "function task":
            increment = flow.function_task(increment)

        results = await asyncio.gather(
            *(increment(1) for _ in range(10)), return_exceptions=True
        )

        assert len(integration.academy_manager.running()) == 1
        assert results == [None] * 10
        get_count = integration.create_agent_task(
            Counter, "get_count", agent_id="shared"
        )
        assert await get_count() == 10


async def test_first_call_after_launch_succeeds_without_startup_delay(
    flow: WorkflowEngine,
) -> None:
    """Defect 5: the launch waited a fixed time instead of for the agent."""
    async with AcademyIntegration(flow) as integration:
        get_count = flow.function_task(
            integration.create_agent_task(
                SlowStartCounter, "get_count", startup_delay=0
            )
        )

        assert await get_count() == 0
        assert EVENTS == ["start"]


async def test_context_leaves_logging_configuration_unchanged(
    flow: WorkflowEngine,
) -> None:
    """Defect 6: entering the context called Academy's init_logging."""
    # Start with warnings not captured, whatever an earlier test left behind.
    logging.captureWarnings(False)
    root = logging.getLogger()
    before = (root.level, list(root.handlers), warnings.showwarning)

    async with AcademyIntegration(flow):
        assert (root.level, list(root.handlers), warnings.showwarning) == before

    assert (root.level, list(root.handlers), warnings.showwarning) == before


async def test_leaving_context_shuts_down_every_agent(flow: WorkflowEngine) -> None:
    async with AcademyIntegration(flow) as integration:
        for i in range(3):
            get_count = flow.function_task(
                integration.create_agent_task(
                    Counter, "get_count", agent_id=f"counter_{i}"
                )
            )
            assert await get_count() == 0
        assert EVENTS == ["start"] * 3

    assert EVENTS.count("stop") == 3
