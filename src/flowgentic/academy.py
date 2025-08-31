"""
Integration layer between Academy and AsyncFlow projects.
This allows Academy agents to be used as workflow tasks in AsyncFlow.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Type, Union, Callable
from concurrent.futures import ThreadPoolExecutor

from academy.agent import Agent
from academy.exchange.local import LocalExchangeFactory
from academy.handle import Handle
from academy.logging import init_logging
from academy.manager import Manager

from radical.asyncflow import WorkflowEngine


class AcademyIntegration:
    """
    Integration layer that creates a bridge between Academy agents and AsyncFlow workflows.
    This allows Academy agents to be used as workflow tasks.
    """

    def __init__(self, workflow_engine: WorkflowEngine):
        self.workflow_engine = workflow_engine
        self.academy_manager: Optional[Manager] = None
        self.launched_agents: Dict[str, Handle] = {}
        self._manager_context = None
        self._agent_startup_delay = 0.1  # Give loops time to start

    async def __aenter__(self):
        """Async context manager entry - initialize Academy manager."""
        init_logging(logging.INFO)

        self._manager_context = await Manager.from_exchange_factory(
            factory=LocalExchangeFactory(),
            executors=ThreadPoolExecutor(),
        )
        self.academy_manager = await self._manager_context.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - cleanup Academy manager."""
        if self._manager_context:
            await self._manager_context.__aexit__(exc_type, exc_val, exc_tb)

    def create_agent_task(self,
                         agent_class: Type[Agent],
                         action_name: str,
                         agent_id: Optional[str] = None,
                         agent_args: tuple = (),
                         agent_kwargs: dict = None,
                         startup_delay: Optional[float] = None) -> Callable:
        """
        Create a workflow task that executes an Academy agent action.

        Args:
            agent_class: The Academy Agent class
            action_name: Name of the action method to call
            agent_id: Optional custom agent ID (defaults to class name)
            agent_args: Arguments to pass to agent constructor
            agent_kwargs: Keyword arguments to pass to agent constructor
            startup_delay: Time to wait after launching agent (for loops to start)

        Returns:
            A workflow task function that can be used with @flow.block decorator
        """
        if agent_kwargs is None:
            agent_kwargs = {}

        agent_id = agent_id or agent_class.__name__
        effective_startup_delay = startup_delay if startup_delay is not None else self._agent_startup_delay

        async def agent_task(*task_args, **task_kwargs):
            """The actual task that will be executed in the workflow."""
            # Launch agent if not already launched
            if agent_id not in self.launched_agents:
                if not self.academy_manager:
                    raise RuntimeError("Academy manager not initialized. Use 'async with' context.")

                agent_handle = await self.academy_manager.launch(
                    agent_class,
                    args=agent_args,
                    kwargs=agent_kwargs
                )
                self.launched_agents[agent_id] = agent_handle

                # Give the agent time to start up, especially for loops
                if effective_startup_delay > 0:
                    await asyncio.sleep(effective_startup_delay)

            # Get the agent handle
            agent_handle = self.launched_agents[agent_id]

            # Call the action method
            action_method = getattr(agent_handle, action_name)
            future = await action_method(*task_args, **task_kwargs)
            result = await future

            return result

        # Set metadata for debugging
        agent_task.__name__ = f"{agent_class.__name__}_{action_name}"
        agent_task.__qualname__ = f"{agent_class.__name__}.{action_name}"

        return agent_task

    async def ensure_agent_loops_running(self, agent_id: str, wait_time: float = 1.0):
        """
        Ensure that loops for a specific agent are running by waiting.

        Args:
            agent_id: The ID of the agent
            wait_time: Time to wait for loops to execute
        """
        if agent_id in self.launched_agents:
            await asyncio.sleep(wait_time)
        else:
            raise ValueError(f"Agent {agent_id} not found in launched agents")

    async def wait_for_all_agent_loops(self, wait_time: float = 1.0):
        """
        Wait for all launched agent loops to execute.

        Args:
            wait_time: Time to wait for loops to execute
        """
        if self.launched_agents:
            await asyncio.sleep(wait_time)

    def create_workflow_from_academy_chain(self,
                                         agents_and_actions: List[tuple],
                                         workflow_name: str = "academy_workflow"):
        """
        Create a complete workflow from a chain of Academy agents.

        Args:
            agents_and_actions: List of (agent_class, action_name, args, kwargs) tuples
            workflow_name: Name for the workflow

        Returns:
            A function that creates and executes the workflow
        """

        async def workflow_func(*initial_args, **initial_kwargs):
            """Execute the complete workflow chain."""
            tasks = []

            # Create all tasks
            for i, (agent_class, action_name, args, kwargs) in enumerate(agents_and_actions):
                agent_task = self.create_agent_task(
                    agent_class,
                    action_name,
                    agent_id=f"{workflow_name}_{agent_class.__name__}_{i}",
                    agent_args=args or (),
                    agent_kwargs=kwargs or {}
                )

                # Wrap with workflow engine block decorator
                workflow_task = self.workflow_engine.block(agent_task)
                tasks.append(workflow_task)

            # Chain the tasks together
            result = None
            for i, task in enumerate(tasks):
                if i == 0:
                    # First task gets initial arguments
                    result = task(*initial_args, **initial_kwargs)
                else:
                    # Subsequent tasks get result from previous task
                    result = task(result)

                # Wait for result before proceeding to next task
                result = await result

            return result

        workflow_func.__name__ = workflow_name
        return workflow_func
