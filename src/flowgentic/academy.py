"""
Academy agents' actions as AsyncFlow tasks.

`AcademyIntegration` starts an Academy manager and returns functions that call
its agents' actions from any AsyncFlow task: a block, on the manager's event
loop, or a function task on a thread-pool backend, in a worker thread.
"""

import asyncio
import contextlib
from typing import Any, Awaitable, Callable, Coroutine, Dict, Optional, Type, TypeVar

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
	Calls Academy agents' actions from AsyncFlow tasks.

	Entering the context starts an Academy manager, with a local exchange, on
	the running event loop; leaving it shuts down every agent it launched.
	Agents run on that loop too, as Academy's manager runs them by default, so
	an agent's blocking work belongs in `Agent.agent_run_sync`.

	Every launch and action runs on the manager's loop, where its exchange
	client and the agents' handles live. A function task's worker thread runs
	its own loop, so its calls are submitted to the manager's loop. Process-pool
	and remote backends cannot reach the manager.
	"""

	def __init__(self) -> None:
		self._manager: Optional[Manager] = None
		self._loop: Optional[asyncio.AbstractEventLoop] = None
		self._specs: Dict[str, tuple] = {}
		self._handles: Dict[str, Handle] = {}
		self._launch_locks: Dict[str, asyncio.Lock] = {}

	async def __aenter__(self) -> "AcademyIntegration":
		if self._manager is not None:
			raise RuntimeError("AcademyIntegration is already entered")
		# No executor: agents run on this loop. On a thread pool, each running
		# agent would hold a worker for its whole life, and agents beyond the
		# pool's size would never start.
		manager = await Manager.from_exchange_factory(factory=LocalExchangeFactory())
		self._manager = await manager.__aenter__()
		self._loop = asyncio.get_running_loop()
		return self

	async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
		try:
			await self._manager.__aexit__(exc_type, exc_val, exc_tb)
		finally:
			self._manager = None
			self._loop = None
			self._handles.clear()
			self._launch_locks.clear()

	def create_agent_task(
		self,
		agent_class: Type[Agent],
		action_name: str,
		*,
		agent_id: Optional[str] = None,
		args: tuple = (),
		kwargs: Optional[dict] = None,
		startup_timeout: Optional[float] = 60.0,
	) -> Callable[..., Awaitable[Any]]:
		"""
		Return a coroutine function that calls one action of an agent.

		The first call for an `agent_id` launches the agent and waits until it
		answers a ping, which it does once `agent_on_startup` has returned.
		Concurrent first calls launch it once. Every function created for the
		same `agent_id` calls the same agent, so they must name the same class
		and constructor arguments.

		The function can be created outside the context; it can be called only
		while the context is active.

		Args:
		    agent_class: The Academy `Agent` subclass.
		    action_name: The name of the `@action` method to call.
		    agent_id: Names the agent within this integration; defaults to the
		        class name.
		    args: Positional arguments for the agent's constructor.
		    kwargs: Keyword arguments for the agent's constructor.
		    startup_timeout: Seconds to wait for a new agent to answer; None
		        waits indefinitely. On timeout the agent is shut down and the
		        call raises `asyncio.TimeoutError`.

		Returns:
		    A coroutine function that passes its arguments to the action and
		    returns the action's result.

		Raises:
		    ValueError: If `agent_id` already names an agent with another class
		        or other constructor arguments.
		"""
		agent_id = agent_id or agent_class.__name__
		spec = (agent_class, tuple(args), dict(kwargs or {}))
		known = self._specs.setdefault(agent_id, spec)
		if known != spec:
			raise ValueError(
				f"Agent ID {agent_id!r} already names a {known[0].__name__} agent "
				"with other constructor arguments"
			)

		async def call_action(action_args: tuple, action_kwargs: dict) -> Any:
			handle = await self._agent_handle(agent_id, spec, startup_timeout)
			return await handle.action(action_name, *action_args, **action_kwargs)

		async def agent_task(*action_args: Any, **action_kwargs: Any) -> Any:
			return await self._on_manager_loop(call_action, action_args, action_kwargs)

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
		if self._manager is None:
			raise RuntimeError(
				"AcademyIntegration is not entered: call agents inside "
				"'async with AcademyIntegration()'"
			)
		if asyncio.get_running_loop() is self._loop:
			return await coroutine_function(*args)
		future = asyncio.run_coroutine_threadsafe(coroutine_function(*args), self._loop)
		return await asyncio.wrap_future(future)

	async def _agent_handle(
		self, agent_id: str, spec: tuple, startup_timeout: Optional[float]
	) -> Handle:
		"""Return the handle of `agent_id`, launching the agent on first use.

		Runs on the manager's loop. One lock per agent ID makes concurrent first
		calls launch one agent, while different agents launch side by side.
		"""
		handle = self._handles.get(agent_id)
		if handle is not None:
			return handle

		lock = self._launch_locks.setdefault(agent_id, asyncio.Lock())
		async with lock:
			handle = self._handles.get(agent_id)
			if handle is not None:
				return handle

			agent_class, args, kwargs = spec
			launched = await self._manager.launch(
				agent_class, args=args, kwargs=kwargs, name=agent_id
			)
			# Bind the handle to the manager's exchange client: Academy otherwise
			# finds the client through its exchange_context variable, which
			# AsyncFlow tasks do not carry.
			handle = Handle(
				launched.agent_id,
				exchange=self._manager.exchange_client,
				ignore_context=True,
			)
			try:
				await handle.ping(timeout=startup_timeout)
			except BaseException:
				with contextlib.suppress(AgentTerminatedError):
					await handle.shutdown()
				raise
			self._handles[agent_id] = handle
			return handle
