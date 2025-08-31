from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from academy.agent import action
from academy.agent import Agent
from academy.exchange.local import LocalExchangeFactory
from academy.logging import init_logging
from academy.manager import Manager

from radical.asyncflow import WorkflowEngine
from radical.asyncflow import ConcurrentExecutionBackend

from flowcademy.academy import AcademyIntegrationAcademyIntegration

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


async def main() -> int:
    """
    Original Academy example converted to use the integration layer.
    This demonstrates how Academy agents can be used as workflow tasks.
    """

    # Create workflow engine
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())
    flow = await WorkflowEngine.create(backend=backend)

    # Create integration layer
    async with AcademyIntegration(flow) as integration:

        # Create workflow tasks from Academy Counter agent
        counter_increment_task = integration.create_agent_task(Counter, 'increment')
        counter_get_count_task = integration.create_agent_task(Counter, 'get_count')

        # Create workflow blocks that wrap the Academy agent actions
        @flow.function_task
        async def increment_block() -> None:
            return await counter_increment_task(1)

        @flow.function_task
        async def get_count_block() -> int:
            return await counter_get_count_task()

        # Execute the workflow - same logic as original but using workflow blocks

        # Get initial count
        initial_count = get_count_block()
        count_result = await initial_count
        assert count_result == 0
        print(f"Initial count: {count_result}")

        # Increment counter
        increment_op = increment_block()
        await increment_op
        print("Counter incremented by 1")

        # Get final count
        final_count = get_count_block()
        count_result = await final_count
        assert count_result == 1
        print(f"Final count: {count_result}")

    await flow.shutdown()
    return 0


async def main_with_dependencies() -> int:
    """
    Enhanced example showing workflow dependencies and multiple operations.
    This demonstrates the power of combining Academy agents with AsyncFlow workflows.
    """

    # Create workflow engine
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())
    flow = await WorkflowEngine.create(backend=backend)

    async with AcademyIntegration(flow) as integration:

        # Create workflow tasks from Academy Counter agent
        counter_increment_task = integration.create_agent_task(Counter, 'increment')
        counter_get_count_task = integration.create_agent_task(Counter, 'get_count')

        # Create workflow blocks with dependencies
        @flow.function_task
        async def get_initial_count() -> int:
            return await counter_get_count_task()

        @flow.function_task
        async def increment_by_5() -> None:
            return await counter_increment_task(5)

        @flow.function_task
        async def increment_by_3() -> None:
            return await counter_increment_task(3)

        @flow.function_task
        async def get_final_count() -> int:
            return await counter_get_count_task()

        @flow.function_task
        async def verify_count(expected: int) -> bool:
            actual = await counter_get_count_task()
            return actual == expected

        # Execute workflow with dependencies
        print("Starting enhanced workflow...")

        # Step 1: Get initial count
        initial = get_initial_count()
        initial_result = await initial
        print(f"Initial count: {initial_result}")

        # Step 2: Increment operations (can run in parallel since they both depend on initial)
        inc5 = increment_by_5()
        inc3 = increment_by_3()

        # Wait for both increments to complete
        await inc5
        await inc3
        print("Both increment operations completed")

        # Step 3: Get final count (depends on both increments)
        final = get_final_count()
        final_result = await final
        print(f"Final count: {final_result}")

        # Step 4: Verify the result
        verification = verify_count(8)  # 0 + 5 + 3 = 8
        is_correct = await verification
        print(f"Count verification: {is_correct}")

        assert final_result == 8
        assert is_correct

    await flow.shutdown()
    return 0


async def main_concurrent_counters() -> int:
    """
    Example showing multiple counter agents working concurrently.
    This demonstrates how to run multiple Academy workflows simultaneously.
    """

    # Create workflow engine
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())
    flow = await WorkflowEngine.create(backend=backend)

    async with AcademyIntegration(flow) as integration:

        async def create_counter_workflow(counter_id: int, increment_value: int):
            """Create a workflow for a single counter."""

            # Create tasks with unique agent IDs
            counter_increment_task = integration.create_agent_task(
                Counter, 'increment',
                agent_id=f"counter_{counter_id}"
            )
            counter_get_count_task = integration.create_agent_task(
                Counter, 'get_count',
                agent_id=f"counter_{counter_id}"
            )

            # Create workflow blocks
            @flow.function_task
            async def increment_counter() -> None:
                return await counter_increment_task(increment_value)

            @flow.function_task
            async def get_counter_result() -> int:
                return await counter_get_count_task()

            # Execute workflow
            increment_op = increment_counter()
            await increment_op

            final_count = get_counter_result()
            result = await final_count

            return f"Counter {counter_id}: {result}"

        # Run multiple counter workflows concurrently
        print("Starting concurrent counter workflows...")

        # Create 10 counter workflows with different increment values
        workflows = [
            create_counter_workflow(i, i * 2)  # Counter i increments by i*2
            for i in range(1, 11)
        ]

        # Execute all workflows concurrently
        results = await asyncio.gather(*workflows)

        print("All counter workflows completed:")
        for result in results:
            print(f"  {result}")

    await flow.shutdown()
    return 0


if __name__ == '__main__':

    print("=== Basic Counter Example ===")
    result1 = asyncio.run(main())

    print("\n=== Enhanced Counter with Dependencies ===")
    result2 = asyncio.run(main_with_dependencies())

    print("\n=== Concurrent Counter Workflows ===")
    result3 = asyncio.run(main_concurrent_counters())

    print(f"\nAll examples completed successfully!")
    raise SystemExit(max(result1, result2, result3))
