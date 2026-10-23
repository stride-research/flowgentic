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
	async with AcademyIntegration() as academy:
		increment = flow.function_task(academy.create_agent_task(Counter, "increment"))
		get_count = flow.function_task(academy.create_agent_task(Counter, "get_count"))

		await increment(2)
		assert await get_count() == 2


async def test_action_returns_result_through_block(flow: WorkflowEngine) -> None:
	"""Defect 2: a block runs on the engine's loop, outside the manager's context."""
	async with AcademyIntegration() as academy:
		scale = flow.block(
			academy.create_agent_task(Scaler, "scale", kwargs={"factor": 3})
		)

		assert await scale(2) == 6


async def test_action_returning_none(flow: WorkflowEngine) -> None:
	"""Defect 3: the result was awaited a second time."""
	async with AcademyIntegration() as academy:
		increment = academy.create_agent_task(Counter, "increment")

		assert await increment(1) is None
		assert await flow.function_task(increment)(1) is None


@pytest.mark.parametrize("caller", ["manager loop", "function task"])
async def test_concurrent_first_calls_launch_one_agent(
	flow: WorkflowEngine, caller: str, remote_registration: None
) -> None:
	"""Defect 4: the membership check and the launch were split by an await."""
	async with AcademyIntegration() as academy:
		increment = academy.create_agent_task(Counter, "increment", agent_id="shared")
		get_count = academy.create_agent_task(Counter, "get_count", agent_id="shared")
		if caller == "function task":
			increment = flow.function_task(increment)

		results = await asyncio.gather(
			*(increment(1) for _ in range(10)), return_exceptions=True
		)

		assert results == [None] * 10
		assert await get_count() == 10

	# Leaving the context waits for every launched agent to stop.
	assert EVENTS.count("start") == 1


async def test_first_call_waits_for_agent_startup(flow: WorkflowEngine) -> None:
	"""Defect 5: the launch waited a fixed time instead of for the agent."""
	async with AcademyIntegration() as academy:
		get_count = flow.function_task(
			academy.create_agent_task(SlowStartCounter, "get_count")
		)

		assert await get_count() == 0
		assert EVENTS == ["start"]


async def test_context_leaves_logging_configuration_unchanged() -> None:
	"""Defect 6: entering the context called Academy's init_logging."""
	# Start with warnings not captured, whatever an earlier test left behind.
	logging.captureWarnings(False)
	root = logging.getLogger()
	before = (root.level, list(root.handlers), warnings.showwarning)

	async with AcademyIntegration():
		assert (root.level, list(root.handlers), warnings.showwarning) == before

	assert (root.level, list(root.handlers), warnings.showwarning) == before


async def test_leaving_context_shuts_down_every_agent(flow: WorkflowEngine) -> None:
	async with AcademyIntegration() as academy:
		for i in range(3):
			get_count = flow.function_task(
				academy.create_agent_task(Counter, "get_count", agent_id=f"counter_{i}")
			)
			assert await get_count() == 0
		assert EVENTS == ["start"] * 3

	assert EVENTS.count("stop") == 3


async def test_agent_that_does_not_answer_in_time_is_shut_down(
	flow: WorkflowEngine,
) -> None:
	async with AcademyIntegration() as academy:
		get_count = flow.function_task(
			academy.create_agent_task(
				SlowStartCounter, "get_count", startup_timeout=0.05
			)
		)

		# The built-in TimeoutError from Python 3.11.
		with pytest.raises(asyncio.TimeoutError):
			await get_count()

	assert EVENTS == ["start", "stop"]


async def test_agents_outnumbering_a_thread_pool_all_answer() -> None:
	"""Defect 9: each agent held a worker of a default thread pool for its life."""
	# A default ThreadPoolExecutor has at most 32 workers.
	agents = 33
	async with AcademyIntegration() as academy:
		get_counts = [
			academy.create_agent_task(
				Counter, "get_count", agent_id=f"counter_{i}", startup_timeout=5
			)
			for i in range(agents)
		]

		assert await asyncio.gather(*(task() for task in get_counts)) == [0] * agents


async def test_one_agent_id_names_one_agent() -> None:
	async with AcademyIntegration() as academy:
		academy.create_agent_task(
			Scaler, "scale", agent_id="scaler", kwargs={"factor": 2}
		)
		academy.create_agent_task(
			Scaler, "scale", agent_id="scaler", kwargs={"factor": 2}
		)

		with pytest.raises(ValueError, match="scaler"):
			academy.create_agent_task(
				Scaler, "scale", agent_id="scaler", kwargs={"factor": 3}
			)
		with pytest.raises(ValueError, match="scaler"):
			academy.create_agent_task(Counter, "get_count", agent_id="scaler")


async def test_calls_need_an_active_context() -> None:
	academy = AcademyIntegration()
	get_count = academy.create_agent_task(Counter, "get_count")

	with pytest.raises(RuntimeError, match="async with"):
		await get_count()

	async with academy:
		assert await get_count() == 0

	with pytest.raises(RuntimeError, match="async with"):
		await get_count()


async def test_context_cannot_be_entered_twice_at_once() -> None:
	academy = AcademyIntegration()
	async with academy:
		with pytest.raises(RuntimeError, match="already"):
			async with academy:
				pass

	# Once left, the same object can be entered again.
	async with academy:
		assert await academy.create_agent_task(Counter, "get_count")() == 0
