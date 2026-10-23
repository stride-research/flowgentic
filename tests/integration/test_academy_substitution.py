"""Academy substitution check: concurrent counters with and without Flowgentic.

The workflow is example 1's concurrent counters: ten Counter agents, each
incremented in one AsyncFlow function task and read back in another. The
Flowgentic version goes through ``AcademyIntegration``; the direct version
calls Academy itself, with the loop and handle handling written in this module.
``docs/evaluation/academy.md`` cites these tests as the evidence for each
criterion.
"""

# ruff: noqa: S101

from __future__ import annotations

import ast
import asyncio
import inspect
import io
import textwrap
import threading
import time
import tokenize
from collections import Counter as Census
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncIterator, Awaitable, Callable

import pytest
from academy.agent import Agent, action
from academy.exception import ExchangeClientNotFoundError
from academy.exchange.local import LocalExchangeFactory
from academy.handle import Handle
from academy.manager import Manager
from radical.asyncflow import LocalExecutionBackend, WorkflowEngine

from flowgentic.academy import AcademyIntegration

COUNTERS = 10
EXPECTED = [f"Counter {i}: {i * 2}" for i in range(1, COUNTERS + 1)]

# Recorded by the agents, which run in this process.
EVENTS: list[str] = []
ACTION_THREADS: set[str] = set()


class Counter(Agent):
    count: int

    async def agent_on_startup(self) -> None:
        self.count = 0
        EVENTS.append("start")

    async def agent_on_shutdown(self) -> None:
        EVENTS.append("stop")

    @action
    async def increment(self, value: int = 1) -> None:
        ACTION_THREADS.add(threading.current_thread().name)
        self.count += value

    @action
    async def get_count(self) -> int:
        ACTION_THREADS.add(threading.current_thread().name)
        return self.count


async def flowgentic_counters(flow: WorkflowEngine) -> list[str]:
    """Example 1's concurrent counters through AcademyIntegration."""
    async with AcademyIntegration(flow) as integration:

        async def counter_workflow(counter_id: int, increment_value: int) -> str:
            agent_id = f"counter_{counter_id}"
            increment = integration.create_agent_task(
                Counter, "increment", agent_id=agent_id
            )
            get_count = integration.create_agent_task(
                Counter, "get_count", agent_id=agent_id
            )

            @flow.function_task
            async def increment_counter() -> None:
                return await increment(increment_value)

            @flow.function_task
            async def get_counter_result() -> int:
                return await get_count()

            await increment_counter()
            return f"Counter {counter_id}: {await get_counter_result()}"

        return await asyncio.gather(
            *(counter_workflow(i, i * 2) for i in range(1, COUNTERS + 1))
        )


async def direct_counters(flow: WorkflowEngine) -> list[str]:
    """The same workflow with direct Academy calls inside AsyncFlow tasks."""
    loop = asyncio.get_running_loop()

    async def on_manager_loop(coroutine: Awaitable[Any]) -> Any:
        # Handles and the exchange client belong to the manager's loop; a
        # function task runs in a worker thread, under its own loop.
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        return await asyncio.wrap_future(future)

    async with await Manager.from_exchange_factory(
        factory=LocalExchangeFactory()
    ) as manager:
        handles = {}
        for counter_id in range(1, COUNTERS + 1):
            launched = await manager.launch(Counter, name=f"counter_{counter_id}")
            # A handle from launch() finds its exchange client through Academy's
            # exchange_context variable, which AsyncFlow tasks do not carry.
            handles[counter_id] = Handle(
                launched.agent_id, exchange=manager.exchange_client, ignore_context=True
            )

        async def counter_workflow(counter_id: int, increment_value: int) -> str:
            handle = handles[counter_id]

            @flow.function_task
            async def increment_counter() -> None:
                return await on_manager_loop(handle.increment(increment_value))

            @flow.function_task
            async def get_counter_result() -> int:
                return await on_manager_loop(handle.get_count())

            await increment_counter()
            return f"Counter {counter_id}: {await get_counter_result()}"

        return await asyncio.gather(
            *(counter_workflow(i, i * 2) for i in range(1, COUNTERS + 1))
        )


VERSIONS: dict[str, Callable[[WorkflowEngine], Awaitable[list[str]]]] = {
    "flowgentic": flowgentic_counters,
    "direct": direct_counters,
}


def code_lines(function: Callable[..., Any]) -> int:
    """Count a function's lines that hold code: no blanks, comments or docstrings."""
    source = textwrap.dedent(inspect.getsource(function))
    docstrings: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            ast.get_docstring(node) is not None
        ):
            docstrings.update(range(node.body[0].lineno, node.body[0].end_lineno + 1))
    lines: set[int] = set()
    skipped = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type not in skipped:
            lines.update(range(token.start[0], token.end[0] + 1))
    return len(lines - docstrings)


@pytest.fixture(autouse=True)
def clear_records() -> None:
    EVENTS.clear()
    ACTION_THREADS.clear()


@pytest.fixture
async def flow() -> AsyncIterator[WorkflowEngine]:
    backend = await LocalExecutionBackend(ThreadPoolExecutor())
    engine = await WorkflowEngine.create(backend=backend)
    yield engine
    await engine.shutdown()


# Criterion 1: results.
@pytest.mark.parametrize("version", VERSIONS)
async def test_both_versions_return_the_same_results(
    flow: WorkflowEngine, version: str
) -> None:
    assert await VERSIONS[version](flow) == EXPECTED


# Criterion 1: both versions submit the same AsyncFlow tasks.
@pytest.mark.parametrize("version", VERSIONS)
async def test_both_versions_submit_the_same_asyncflow_tasks(
    flow: WorkflowEngine, version: str
) -> None:
    await VERSIONS[version](flow)

    census = Census(
        (component["type"], component["description"]["name"])
        for component in flow.components.values()
    )
    assert census == {
        ("task", "increment_counter"): COUNTERS,
        ("task", "get_counter_result"): COUNTERS,
    }


# Criterion 2: lifecycle.
@pytest.mark.parametrize("version", VERSIONS)
async def test_both_versions_launch_and_shut_down_each_agent_once(
    flow: WorkflowEngine, version: str
) -> None:
    await VERSIONS[version](flow)

    assert EVENTS.count("start") == COUNTERS
    assert EVENTS.count("stop") == COUNTERS


# Criterion 3: calls from worker threads.
async def test_handle_from_launch_fails_in_a_function_task(
    flow: WorkflowEngine,
) -> None:
    async with await Manager.from_exchange_factory(
        factory=LocalExchangeFactory()
    ) as manager:
        handle = await manager.launch(Counter)

        @flow.function_task
        async def get_count() -> int:
            return await handle.get_count()

        with pytest.raises(ExchangeClientNotFoundError):
            await get_count()


# Criterion 3: calls from worker threads.
async def test_bound_handle_on_the_worker_loop_waits_for_that_loop_to_wake(
    flow: WorkflowEngine,
) -> None:
    """Without the hop to the manager's loop, the answer goes unnoticed.

    The agent answers at once, but the worker thread's loop learns of it only
    when something else wakes it: here, the timer of ``wait_for``. With no
    timer, the call never returns.
    """
    wake_after = 1.0
    async with await Manager.from_exchange_factory(
        factory=LocalExchangeFactory()
    ) as manager:
        launched = await manager.launch(Counter)
        handle = Handle(
            launched.agent_id, exchange=manager.exchange_client, ignore_context=True
        )
        await handle.ping()

        @flow.function_task
        async def timed_get_count() -> float:
            start = time.perf_counter()
            assert await asyncio.wait_for(handle.get_count(), wake_after) == 0
            return time.perf_counter() - start

        assert await timed_get_count() >= wake_after * 0.9


# Criterion 4: where the agent's work runs.
@pytest.mark.parametrize("version", VERSIONS)
async def test_actions_run_on_the_managers_loop_not_in_the_task(
    flow: WorkflowEngine, version: str
) -> None:
    await VERSIONS[version](flow)

    assert ACTION_THREADS == {threading.main_thread().name}


# Criterion 4: where the agent's work runs.
async def test_actions_need_no_asyncflow_task() -> None:
    async with await Manager.from_exchange_factory(
        factory=LocalExchangeFactory()
    ) as manager:
        handles = {i: await manager.launch(Counter) for i in range(1, COUNTERS + 1)}

        async def counter_workflow(counter_id: int, increment_value: int) -> str:
            await handles[counter_id].increment(increment_value)
            return f"Counter {counter_id}: {await handles[counter_id].get_count()}"

        results = await asyncio.gather(
            *(counter_workflow(i, i * 2) for i in range(1, COUNTERS + 1))
        )

    assert results == EXPECTED


# Criterion 5: application code.
def test_application_code_lines() -> None:
    assert code_lines(flowgentic_counters) == 21
    assert code_lines(direct_counters) == 27
