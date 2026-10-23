"""Academy agents' actions as AsyncFlow tasks, without Flowgentic.

An Academy handle finds its exchange client through a context variable that
AsyncFlow tasks do not carry, and a function task runs in a worker thread,
under its own event loop. So the application binds each handle to the
manager's exchange client, and awaits each action on the manager's loop.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Coroutine

from academy.agent import action
from academy.agent import Agent
from academy.exchange.local import LocalExchangeFactory
from academy.handle import Handle
from academy.manager import Manager

from radical.asyncflow import WorkflowEngine
from radical.asyncflow import LocalExecutionBackend


# --8<-- [start:agent]
class Counter(Agent):
    count: int

    async def agent_on_startup(self) -> None:
        self.count = 0

    @action
    async def increment(self, value: int = 1) -> None:
        self.count += value

    @action
    async def get_count(self) -> int:
        return self.count
# --8<-- [end:agent]


# --8<-- [start:helpers]
def bind(manager: Manager, launched: Handle) -> Handle:
    """Bind a handle to the manager's exchange client, in any context."""
    return Handle(
        launched.agent_id, exchange=manager.exchange_client, ignore_context=True
    )


async def on_loop(loop: asyncio.AbstractEventLoop, call: Coroutine[Any, Any, Any]) -> Any:
    """Await an action on the manager's loop, from that loop or a worker thread."""
    if asyncio.get_running_loop() is loop:
        return await call
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(call, loop))
# --8<-- [end:helpers]


async def main() -> int:
    """One counter, incremented by function tasks and read by a block."""
    backend = await LocalExecutionBackend(ThreadPoolExecutor())
    flow = await WorkflowEngine.create(backend=backend)
    loop = asyncio.get_running_loop()

    # --8<-- [start:tasks]
    # With no executor, the manager runs its agents on this event loop.
    async with await Manager.from_exchange_factory(
        factory=LocalExchangeFactory()
    ) as manager:
        counter = bind(manager, await manager.launch(Counter))

        @flow.function_task
        async def increment(value: int) -> None:
            return await on_loop(loop, counter.increment(value))

        @flow.block
        async def get_count() -> int:
            return await on_loop(loop, counter.get_count())

        print(f"Initial count: {await get_count()}")
        await increment(1)
        await increment(2)
        print(f"Final count: {await get_count()}")
    # --8<-- [end:tasks]

    await flow.shutdown()
    return 0


async def main_concurrent_counters() -> int:
    """Ten counters, each incremented and read by its own function tasks."""
    backend = await LocalExecutionBackend(ThreadPoolExecutor())
    flow = await WorkflowEngine.create(backend=backend)
    loop = asyncio.get_running_loop()

    async with await Manager.from_exchange_factory(
        factory=LocalExchangeFactory()
    ) as manager:

        async def counter_workflow(counter_id: int, increment_value: int) -> str:
            launched = await manager.launch(Counter, name=f"counter_{counter_id}")
            counter = bind(manager, launched)

            @flow.function_task
            async def increment_counter() -> None:
                return await on_loop(loop, counter.increment(increment_value))

            @flow.function_task
            async def get_counter_result() -> int:
                return await on_loop(loop, counter.get_count())

            await increment_counter()
            return f"Counter {counter_id}: {await get_counter_result()}"

        results = await asyncio.gather(
            *(counter_workflow(i, i * 2) for i in range(1, 11))
        )

    for result in results:
        print(f"  {result}")

    await flow.shutdown()
    return 0


if __name__ == '__main__':

    print("=== An action in a function task and in a block ===")
    result1 = asyncio.run(main())

    print("\n=== Concurrent counters ===")
    result2 = asyncio.run(main_concurrent_counters())

    print("\nAll examples completed successfully!")
    raise SystemExit(max(result1, result2))
