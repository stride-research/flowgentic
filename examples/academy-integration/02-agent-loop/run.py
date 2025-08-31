from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from academy.agent import action
from academy.agent import Agent
from academy.agent import loop
from academy.exchange.local import LocalExchangeFactory
from academy.logging import init_logging
from academy.manager import Manager

from radical.asyncflow import WorkflowEngine
from radical.asyncflow import ConcurrentExecutionBackend

# Import our integration layer (assuming it's in the same directory)
from flowcademy.academy import AcademyIntegrationAcademyIntegration

logger = logging.getLogger(__name__)


class Counter(Agent):
    count: int

    async def agent_on_startup(self) -> None:
        self.count = 0

    @loop
    async def increment(self, shutdown: asyncio.Event) -> None:
        while not shutdown.is_set():
            await asyncio.sleep(1)
            self.count += 1

    @action
    async def get_count(self) -> int:
        return self.count


async def main() -> int:
    """
    Fixed Academy loop example that properly handles loops.
    """
    init_logging(logging.INFO)

    # Create workflow engine
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())
    flow = await WorkflowEngine.create(backend=backend)

    # Create integration layer
    async with AcademyIntegration(flow) as integration:

        # Create workflow tasks from Academy Counter agent
        # Use a longer startup delay to ensure loops are running
        counter_get_count_task = integration.create_agent_task(
            Counter, 'get_count',
            startup_delay=0.5  # Give loops time to start
        )

        # Create workflow blocks that wrap the Academy agent actions
        @flow.function_task
        async def get_count_block() -> int:
            return await counter_get_count_task()

        # First, create the agent by calling get_count once (this triggers agent creation)
        logger.info('Creating Counter agent...')
        initial_count_task = get_count_block()
        initial_count = await initial_count_task
        logger.info(f'Initial count: {initial_count}')

        # Now wait for agent loops to execute
        logger.info('Waiting 2s for agent loops to execute...')
        await asyncio.sleep(2)

        # Get count after loops have run
        count_task = get_count_block()
        count_result = await count_task
        print(f"Final count: {count_result}")

        assert count_result >= 1, f"Count should be at least 1, got {count_result}"
        logger.info('Agent loop executed %s time(s)', count_result)

    await flow.shutdown()
    return 0


async def main_with_monitoring() -> int:
    """
    Enhanced version that monitors the counter over time.
    """
    init_logging(logging.INFO)

    # Create workflow engine
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())
    flow = await WorkflowEngine.create(backend=backend)

    async with AcademyIntegration(flow) as integration:

        # Create workflow tasks from Academy Counter agent
        counter_get_count_task = integration.create_agent_task(
            Counter, 'get_count',
            startup_delay=0.5  # Give loops time to start
        )

        # Create workflow blocks
        @flow.function_task
        async def get_count_with_timestamp() -> tuple[int, float]:
            import time
            count = await counter_get_count_task()
            timestamp = time.time()
            return count, timestamp

        @flow.function_task
        async def log_count(count: int, timestamp: float) -> None:
            logger.info(f'Count at {timestamp}: {count}')

        # Initialize the agent first
        logger.info('Initializing counter agent...')
        init_task = get_count_with_timestamp()
        init_count, init_timestamp = await init_task
        logger.info(f'Initial count: {init_count} at {init_timestamp}')

        # Monitor counter over multiple intervals
        logger.info('Starting counter monitoring...')

        monitoring_results = []

        for i in range(5):  # Monitor 5 times
            logger.info(f'Monitoring iteration {i+1}/5')

            # Wait 1 second between checks
            await asyncio.sleep(1)

            # Get count with timestamp
            count_task = get_count_with_timestamp()
            count, timestamp = await count_task

            # Log the result
            log_task = log_count(count, timestamp)
            await log_task

            monitoring_results.append((count, timestamp))

        # Verify that count increased over time
        first_count = monitoring_results[0][0]
        last_count = monitoring_results[-1][0]

        assert last_count > first_count, f"Counter should have increased: {first_count} -> {last_count}"
        logger.info(f'Counter increased from {first_count} to {last_count} over monitoring period')

    await flow.shutdown()
    return 0


async def main_parallel_monitoring() -> int:
    """
    Advanced example showing parallel monitoring of multiple counter agents.
    """
    init_logging(logging.INFO)

    # Create workflow engine
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())
    flow = await WorkflowEngine.create(backend=backend)

    async with AcademyIntegration(flow) as integration:

        async def monitor_counter(counter_id: int, monitor_duration: int):
            """Monitor a single counter for a specified duration."""

            # Create tasks with unique agent IDs
            counter_get_count_task = integration.create_agent_task(
                Counter, 'get_count',
                agent_id=f"counter_{counter_id}",
                startup_delay=0.5  # Give loops time to start
            )

            @flow.function_task
            async def get_count_for_counter() -> int:
                return await counter_get_count_task()

            # Initialize the counter
            logger.info(f'Initializing counter {counter_id}...')
            init_task = get_count_for_counter()
            init_count = await init_task
            logger.info(f'Counter {counter_id} initialized with count: {init_count}')

            # Wait for the specified duration
            logger.info(f'Waiting {monitor_duration}s for counter {counter_id} to increment...')
            await asyncio.sleep(monitor_duration)

            # Get final count
            count_task = get_count_for_counter()
            final_count = await count_task

            logger.info(f'Counter {counter_id} (monitored for {monitor_duration}s): {final_count}')
            return final_count

        # Start multiple counter monitoring workflows with different durations
        logger.info('Starting parallel counter monitoring...')

        # Create monitoring tasks for different counters with different durations
        monitoring_tasks = [
            monitor_counter(1, 2),  # Monitor counter 1 for 2 seconds
            monitor_counter(2, 3),  # Monitor counter 2 for 3 seconds
            monitor_counter(3, 4),  # Monitor counter 3 for 4 seconds
        ]

        # Execute all monitoring tasks concurrently
        results = await asyncio.gather(*monitoring_tasks)

        logger.info('All monitoring tasks completed:')
        for i, count in enumerate(results, 1):
            logger.info(f'  Counter {i}: {count}')
            assert count >= 1, f"Counter {i} should have incremented at least once"

    await flow.shutdown()
    return 0


if __name__ == '__main__':

    print("=== Basic Loop Counter Example ===")
    result1 = asyncio.run(main())

    print("\n=== Enhanced Counter with Monitoring ===")
    result2 = asyncio.run(main_with_monitoring())

    print("\n=== Parallel Counter Monitoring ===")
    result3 = asyncio.run(main_parallel_monitoring())

    print(f"\nAll examples completed successfully!")
    raise SystemExit(max(result1, result2, result3))
