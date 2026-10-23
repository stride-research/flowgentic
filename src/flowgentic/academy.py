"""
Integration layer between Academy and AsyncFlow projects.
This allows Academy agents to be used as workflow tasks in AsyncFlow.
"""

import asyncio
import contextlib
from typing import (
	Any,
	Awaitable,
	Callable,
	Coroutine,
	Dict,
	List,
	Optional,
	Type,
	TypeVar,
)

from radical.asyncflow import WorkflowEngine

try:
	from academy.agent import Agent
	from academy.exception import AgentTerminatedError
	from academy.exchange.local import LocalExchangeFactory
	from academy.handle import Handle
	from academy.manager import Manager
except ModuleNotFoundError as exc:
	raise ImportError(
		"flowgentic.academy requires academy-py: install Flowgentic with the "
		"'academy' extra, flowgentic[academy]"
	) from exc

T = TypeVar("T")


class AcademyIntegration:
	"""
	Integration layer that creates a bridge between Academy agents and AsyncFlow workflows.
	This allows Academy agents to be used as workflow tasks.

	Entering the context starts an Academy manager, with a local exchange, on
	the running event loop. Agents run on that loop too, as Academy's manager
	runs them by default, so an agent's blocking work belongs in
	`Agent.agent_run_sync`. Every launch and action runs on that loop, where
	the manager's exchange client and the agents' handles live. A task created
	by `create_agent_task` can therefore run as an AsyncFlow block, on the same
	loop, or as a function task on a thread-pool backend, whose worker thread
	runs its own loop. Process-pool and remote backends cannot reach the
	manager.
	"""

	def __init__(self, workflow_engine: WorkflowEngine):
		self.workflow_engine = workflow_engine
		self.academy_manager: Optional[Manager] = None
		self.launched_agents: Dict[str, Handle] = {}
		self._manager_context = None
		self._loop: Optional[asyncio.AbstractEventLoop] = None
		self._launch_locks: Dict[str, asyncio.Lock] = {}
		self._agent_startup_delay = 0.0

	async def __aenter__(self):
		"""Async context manager entry - start the Academy manager on this loop."""
		self._loop = asyncio.get_running_loop()
		# No executor: agents run on this loop. On a thread pool, each running
		# agent would hold a worker for its whole life, and agents beyond the
		# pool's size would never start.
		self._manager_context = await Manager.from_exchange_factory(
			factory=LocalExchangeFactory(),
		)
		self.academy_manager = await self._manager_context.__aenter__()
		return self

	async def __aexit__(self, exc_type, exc_val, exc_tb):
		"""Async context manager exit - shut down every launched agent."""
		if self._manager_context:
			try:
				await self._manager_context.__aexit__(exc_type, exc_val, exc_tb)
			finally:
				self._manager_context = None
				self.academy_manager = None
				self.launched_agents.clear()
				self._launch_locks.clear()

	def create_agent_task(
		self,
		agent_class: Type[Agent],
		action_name: str,
		agent_id: Optional[str] = None,
		agent_args: tuple = (),
		agent_kwargs: dict = None,
		startup_delay: Optional[float] = None,
		startup_timeout: Optional[float] = 60.0,
	) -> Callable[..., Awaitable[Any]]:
		"""
		Create a workflow task that executes an Academy agent action.

		The first call for an `agent_id` launches the agent and waits until it
		answers a ping, which it does once `agent_on_startup` has returned.
		Concurrent first calls launch it once.

		Args:
		    agent_class: The Academy Agent class
		    action_name: Name of the action method to call
		    agent_id: Optional custom agent ID (defaults to class name)
		    agent_args: Arguments to pass to agent constructor
		    agent_kwargs: Keyword arguments to pass to agent constructor
		    startup_delay: Extra time to wait after the agent answers, for
		        agents whose loops need a head start (default 0)
		    startup_timeout: Time to wait for the agent to answer after its
		        launch; None waits indefinitely. On timeout the agent is shut
		        down and the call raises `asyncio.TimeoutError`.

		Returns:
		    A workflow task function that can be used with @flow.block decorator
		"""
		if agent_kwargs is None:
			agent_kwargs = {}

		agent_id = agent_id or agent_class.__name__
		effective_startup_delay = (
			startup_delay if startup_delay is not None else self._agent_startup_delay
		)

		async def launch() -> Handle:
			return await self.academy_manager.launch(
				agent_class, args=agent_args, kwargs=agent_kwargs, name=agent_id
			)

		async def call_action(task_args: tuple, task_kwargs: dict) -> Any:
			handle = await self._agent_handle(
				agent_id, launch, effective_startup_delay, startup_timeout
			)
			return await handle.action(action_name, *task_args, **task_kwargs)

		async def agent_task(*task_args, **task_kwargs):
			"""The actual task that will be executed in the workflow."""
			return await self._on_manager_loop(call_action, task_args, task_kwargs)

		# Set metadata for debugging
		agent_task.__name__ = f"{agent_class.__name__}_{action_name}"
		agent_task.__qualname__ = f"{agent_class.__name__}.{action_name}"

		return agent_task

	async def _on_manager_loop(
		self, coroutine_function: Callable[..., Coroutine[Any, Any, T]], *args: Any
	) -> T:
		"""Run a coroutine function on the manager's event loop and return its result.

		A function task runs in a worker thread under its own event loop; its
		coroutine is submitted to the manager's loop, and the result awaited.
		"""
		if self.academy_manager is None or self._loop is None:
			raise RuntimeError(
				"Academy manager not initialized. Use 'async with' context."
			)
		if asyncio.get_running_loop() is self._loop:
			return await coroutine_function(*args)
		future = asyncio.run_coroutine_threadsafe(coroutine_function(*args), self._loop)
		return await asyncio.wrap_future(future)

	async def _agent_handle(
		self,
		agent_id: str,
		launch: Callable[[], Awaitable[Handle]],
		startup_delay: float,
		startup_timeout: Optional[float],
	) -> Handle:
		"""Return the handle of `agent_id`, launching the agent on first use.

		Runs on the manager's loop. One lock per agent ID makes concurrent first
		calls launch one agent, while different agents launch side by side.
		"""
		handle = self.launched_agents.get(agent_id)
		if handle is not None:
			return handle

		lock = self._launch_locks.setdefault(agent_id, asyncio.Lock())
		async with lock:
			handle = self.launched_agents.get(agent_id)
			if handle is not None:
				return handle

			launched = await launch()
			# Bind the handle to the manager's exchange client: Academy otherwise
			# finds the client through its exchange_context variable, which
			# AsyncFlow tasks do not carry.
			handle = Handle(
				launched.agent_id,
				exchange=self.academy_manager.exchange_client,
				ignore_context=True,
			)
			try:
				await handle.ping(timeout=startup_timeout)
			except BaseException:
				with contextlib.suppress(AgentTerminatedError):
					await handle.shutdown()
				raise
			if startup_delay > 0:
				await asyncio.sleep(startup_delay)
			self.launched_agents[agent_id] = handle
			return handle

	async def ensure_agent_loops_running(self, agent_id: str, wait_time: float = 1.0):
		"""
		Wait `wait_time` seconds if the agent has been launched.

		This only waits: it does not check the agent's loops. A launch already
		waits for the agent to start, after which its loops are scheduled.

		Args:
		    agent_id: The ID of the agent
		    wait_time: Time to wait for loops to execute

		Raises:
		    ValueError: If no agent with this ID has been launched.
		"""
		if agent_id in self.launched_agents:
			await asyncio.sleep(wait_time)
		else:
			raise ValueError(f"Agent {agent_id} not found in launched agents")

	async def wait_for_all_agent_loops(self, wait_time: float = 1.0):
		"""
		Wait `wait_time` seconds if any agent has been launched.

		This only waits: it does not check the agents' loops.

		Args:
		    wait_time: Time to wait for loops to execute
		"""
		if self.launched_agents:
			await asyncio.sleep(wait_time)

	def create_workflow_from_academy_chain(
		self, agents_and_actions: List[tuple], workflow_name: str = "academy_workflow"
	) -> Callable[..., Awaitable[Any]]:
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
			for i, (agent_class, action_name, args, kwargs) in enumerate(
				agents_and_actions
			):
				agent_task = self.create_agent_task(
					agent_class,
					action_name,
					agent_id=f"{workflow_name}_{agent_class.__name__}_{i}",
					agent_args=args or (),
					agent_kwargs=kwargs or {},
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
