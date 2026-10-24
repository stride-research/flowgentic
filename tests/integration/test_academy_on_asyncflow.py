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
import importlib.util
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator

import pytest

# Skip only when academy-py is absent: a broken install must fail, not skip.
if importlib.util.find_spec("academy") is None:
    pytest.skip("needs academy-py, from the examples extra", allow_module_level=True)

from academy.agent import Agent, action  # noqa: E402
from academy.exception import ExchangeClientNotFoundError  # noqa: E402
from academy.exchange.local import LocalExchangeFactory  # noqa: E402
from academy.handle import Handle  # noqa: E402
from academy.manager import Manager  # noqa: E402
from radical.asyncflow import LocalExecutionBackend, WorkflowEngine  # noqa: E402

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

    With academy-py 1.0, the agent answers at once, but the reply reaches the
    worker thread's loop without waking it: the call returns only when
    something else wakes that loop, here a timer, and with nothing to wake it,
    never. The same call hopped to the manager's loop returns at once, so the
    delay comes from the worker's loop, not from Academy. The ``wait_for``
    bound is only a backstop against a hang.
    """
    wake_after = 1.0
    loop = asyncio.get_running_loop()
    async with await manager() as academy:
        launched = await academy.launch(Counter)
        handle = Handle(
            launched.agent_id, exchange=academy.exchange_client, ignore_context=True
        )
        await handle.ping()

        @flow.function_task
        async def timed_get_counts() -> tuple[float, int, float]:
            start = time.perf_counter()
            hop = asyncio.run_coroutine_threadsafe(handle.get_count(), loop)
            await asyncio.wrap_future(hop)
            hopped = time.perf_counter() - start

            asyncio.get_running_loop().call_later(wake_after, lambda: None)
            start = time.perf_counter()
            count = await asyncio.wait_for(handle.get_count(), 10 * wake_after)
            return hopped, count, time.perf_counter() - start

        hopped, count, elapsed = await timed_get_counts()

    assert hopped < wake_after / 2
    assert count == 0
    assert elapsed >= wake_after * 0.9, (
        "The worker's loop no longer waits to be woken: Academy may now deliver "
        "replies thread-safely. Revisit on_loop in "
        "docs/guides/academy-on-asyncflow.md."
    )


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
