"""What the Academy guide claims about calling agents from AsyncFlow tasks.

``docs/guides/academy-on-asyncflow.md`` gives two helpers, a bound handle and
a hop to the manager's event loop, and says why each is needed. These tests
show the failure each one prevents, and where an action runs.
``examples/academy-integration/direct/run.py``, run by
``test_academy_examples.py``, shows the helpers working.
"""

# ruff: noqa: S101

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator

import pytest
from academy.agent import Agent, action
from academy.exception import ExchangeClientNotFoundError
from academy.exchange.local import LocalExchangeFactory
from academy.handle import Handle
from academy.manager import Manager
from radical.asyncflow import LocalExecutionBackend, WorkflowEngine

COUNTERS = 10
EXPECTED = [f"Counter {i}: {i * 2}" for i in range(1, COUNTERS + 1)]

# Recorded by the agents, which run in this process.
ACTION_THREADS: set[str] = set()


class Counter(Agent):
    count: int

    async def agent_on_startup(self) -> None:
        self.count = 0

    @action
    async def increment(self, value: int = 1) -> None:
        ACTION_THREADS.add(threading.current_thread().name)
        self.count += value

    @action
    async def get_count(self) -> int:
        ACTION_THREADS.add(threading.current_thread().name)
        return self.count


@pytest.fixture(autouse=True)
def clear_records() -> None:
    ACTION_THREADS.clear()


@pytest.fixture
async def flow() -> AsyncIterator[WorkflowEngine]:
    backend = await LocalExecutionBackend(ThreadPoolExecutor())
    engine = await WorkflowEngine.create(backend=backend)
    yield engine
    await engine.shutdown()


async def manager() -> Manager:
    """An Academy manager with a local exchange, its agents on this loop."""
    return await Manager.from_exchange_factory(factory=LocalExchangeFactory())


async def test_handle_from_launch_fails_in_a_function_task(
    flow: WorkflowEngine,
) -> None:
    """Why the guide binds each handle to the manager's exchange client."""
    async with await manager() as academy:
        handle = await academy.launch(Counter)

        @flow.function_task
        async def get_count() -> int:
            return await handle.get_count()

        with pytest.raises(ExchangeClientNotFoundError):
            await get_count()


async def test_bound_handle_on_the_worker_loop_waits_for_that_loop_to_wake(
    flow: WorkflowEngine,
) -> None:
    """Why the guide awaits each action on the manager's loop.

    The agent answers at once, but the worker thread's loop learns of it only
    when something else wakes it: here, the timer of ``wait_for``. With no
    timer, the call never returns.
    """
    wake_after = 1.0
    async with await manager() as academy:
        launched = await academy.launch(Counter)
        handle = Handle(
            launched.agent_id, exchange=academy.exchange_client, ignore_context=True
        )
        await handle.ping()

        @flow.function_task
        async def timed_get_count() -> float:
            start = time.perf_counter()
            assert await asyncio.wait_for(handle.get_count(), wake_after) == 0
            return time.perf_counter() - start

        assert await timed_get_count() >= wake_after * 0.9


async def test_actions_run_on_the_managers_loop_not_in_the_task(
    flow: WorkflowEngine,
) -> None:
    """The function task only waits: the action runs where the agent runs."""
    loop = asyncio.get_running_loop()
    async with await manager() as academy:
        launched = await academy.launch(Counter)
        handle = Handle(
            launched.agent_id, exchange=academy.exchange_client, ignore_context=True
        )

        @flow.function_task
        async def increment() -> str:
            call = handle.increment(1)
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(call, loop))
            return threading.current_thread().name

        task_thread = await increment()

    assert task_thread != threading.main_thread().name
    assert ACTION_THREADS == {threading.main_thread().name}


async def test_actions_need_no_asyncflow_task() -> None:
    """Awaited in the manager's context, the actions give the same results."""
    async with await manager() as academy:
        handles = {i: await academy.launch(Counter) for i in range(1, COUNTERS + 1)}

        async def counter_workflow(counter_id: int, increment_value: int) -> str:
            await handles[counter_id].increment(increment_value)
            return f"Counter {counter_id}: {await handles[counter_id].get_count()}"

        results = await asyncio.gather(
            *(counter_workflow(i, i * 2) for i in range(1, COUNTERS + 1))
        )

    assert results == EXPECTED
