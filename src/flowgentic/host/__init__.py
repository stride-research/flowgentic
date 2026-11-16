"""The agent host: runs many agents in one process, at HPC scale.

An ``AgentHost`` runs agents of any framework through a ``Runner``, the small
adapter each framework provides (``flowgentic.langgraph.GraphRunner`` for
LangGraph). It owns what makes thousands of agents in one Python process work:

- **The collector.** CPython's cyclic collector, at its default settings,
  spends most of a busy agent process's time scanning the agents' live
  objects. The host raises its first threshold while it runs, and freezes the
  objects that exist once agents have started, so later collections skip them.
  It restores both when ``run`` returns.
- **A cap on live agents.** At most ``max_live_agents`` agents are live at
  once, suspended ones included; more are accepted, and each starts when a live
  one ends. The cap bounds the event loop's lag and the collector's pauses.
- **Staggered starts.** Agents start in batches, so they do not all reach
  their first model call or their first suspension at the same moment.
- **Suspension.** When an agent's runner reports that the agent waits on tasks,
  the host holds no state of the agent's, only its id and the tasks' futures:
  the framework keeps the state in its checkpoint. The host awaits the tasks and
  resumes the agent. Whatever submits a task registers its future under the
  task's key with ``register_pending``, and the runner names those keys.
- **Accounting.** ``AgentStates`` counts the agents in each state, from which a
  campaign reports how many agents are working, waiting, and making model
  requests. One agent's failure never stops the others.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
import traceback
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from flowgentic import _agents, _telemetry
from flowgentic._agents import CATEGORY, AgentState, AgentStates, register_pending
from flowgentic.host._probes import CollectorPauses, LoopLag

__all__ = [
	"CATEGORY",
	"Agent",
	"AgentHost",
	"AgentResult",
	"AgentState",
	"AgentStates",
	"Collector",
	"Finished",
	"HostReport",
	"Interrupted",
	"Runner",
	"Stagger",
	"Suspended",
	"register_pending",
]

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Agent:
	"""One agent to run: its id (the thread id, for LangGraph), its input, and
	run configuration merged over the host's."""

	id: str
	input: Any
	config: dict | None = None


@dataclass(frozen=True, slots=True)
class Finished:
	"""The agent ended; ``output`` is what its framework returned."""

	output: Any


@dataclass(frozen=True, slots=True)
class Suspended:
	"""The agent waits on tasks: pairs of (resume id, task key). Each key names
	a future registered with ``register_pending`` in this process."""

	waits: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class Interrupted:
	"""The agent stopped for a reason the host cannot resolve, such as a
	human-in-the-loop interrupt."""

	interrupts: tuple[Any, ...]


Step = Finished | Suspended | Interrupted


class Runner(Protocol):
	"""How a host drives one framework's agents."""

	async def start(self, agent: Agent) -> Step:
		"""Run the agent from its input until it ends, suspends or stops."""
		...

	async def resume(self, agent: Agent, done: dict[str, Any]) -> Step:
		"""Resume a suspended agent. ``done`` maps each resume id to a marker
		that its task has ended; the task's outcome is read from the task."""
		...


@dataclass(frozen=True, slots=True)
class Collector:
	"""The cyclic collector's settings while a host runs.

	The first threshold rises to ``threshold`` (CPython's default is 700), and
	with ``freeze`` the objects alive once agents have started are moved out of
	the collector's reach. The defaults were measured on CPython 3.12.
	"""

	threshold: int = 100_000
	freeze: bool = True


@dataclass(frozen=True, slots=True)
class Stagger:
	"""Start agents ``batch`` at a time, ``interval_s`` seconds apart."""

	batch: int = 100
	interval_s: float = 0.5

	def __post_init__(self) -> None:
		if self.batch < 1 or self.interval_s < 0:
			raise ValueError("a stagger needs batch >= 1 and interval_s >= 0")


@dataclass(slots=True)
class AgentResult:
	"""How one agent's run ended. Its output goes to ``on_done``, not here."""

	id: str
	status: str = "pending"  # done, failed or interrupted
	error: str | None = None
	t_start: float = 0.0
	t_end: float = 0.0
	suspensions: int = 0
	interrupts: tuple[Any, ...] = ()


@dataclass(slots=True)
class HostReport:
	"""What a run did, and what it cost the process."""

	agents: list[AgentResult]
	states: dict
	loop_lag: dict
	collector: dict
	wall_s: float
	errors: dict[str, int] = field(default_factory=dict)

	@property
	def finished(self) -> int:
		return sum(1 for a in self.agents if a.status == "done")


class AgentHost:
	"""Runs agents through ``runner``; see the module's docstring."""

	def __init__(
		self,
		runner: Runner,
		*,
		max_live_agents: int = 5_000,
		stagger: Stagger = Stagger(),
		collector: Collector | None = Collector(),
		on_done: Callable[[AgentResult, Any], None] | None = None,
		states: AgentStates | None = None,
	) -> None:
		if max_live_agents < 1:
			raise ValueError("max_live_agents must be at least 1")
		self.runner = runner
		self.max_live_agents = max_live_agents
		self.stagger = stagger
		self.collector = collector
		self.on_done = on_done
		# the application may pass its own, to sample it while agents run
		self.states = states if states is not None else AgentStates()

	async def run(self, agents: Iterable[Agent]) -> HostReport:
		"""Run every agent to its end, and report."""
		work = list(agents)
		for agent in work:
			self.states.add(agent.id)
		results = {a.id: AgentResult(a.id) for a in work}
		live = asyncio.Semaphore(self.max_live_agents)
		lag, pauses = LoopLag(), CollectorPauses()
		saved = self._collector_start()
		freeze: dict = {}
		lag.start()
		pauses.start()
		t0 = time.monotonic()
		tasks: list[asyncio.Task] = []
		try:
			first = min(len(work), self.max_live_agents)
			loop = asyncio.get_running_loop()
			for i in range(0, first, self.stagger.batch):
				if i:
					await asyncio.sleep(self.stagger.interval_s)
				for agent in work[i : min(i + self.stagger.batch, first)]:
					tasks.append(loop.create_task(self._live(agent, results, live)))
			await asyncio.sleep(0)
			freeze = self._collector_started(t0)
			for agent in work[first:]:
				tasks.append(loop.create_task(self._live(agent, results, live)))
			await asyncio.gather(*tasks)
		except BaseException:
			for task in tasks:
				task.cancel()
			await asyncio.gather(*tasks, return_exceptions=True)
			raise
		finally:
			wall = time.monotonic() - t0
			lag.stop()
			pauses.stop()
			self._collector_end(saved)
		errors: dict[str, int] = {}
		for r in results.values():
			if r.status == "failed" and r.error:
				kind = r.error.split(":", 1)[0]
				errors[kind] = errors.get(kind, 0) + 1
		return HostReport(
			agents=list(results.values()),
			states=self.states.summary(),
			loop_lag=lag.summary(),
			collector={
				**pauses.summary(),
				"settings": (
					None
					if self.collector is None
					else {
						"threshold": self.collector.threshold,
						"freeze": self.collector.freeze,
					}
				),
				"freeze": freeze,
			},
			wall_s=round(wall, 3),
			errors=errors,
		)

	async def _live(
		self, agent: Agent, results: dict[str, AgentResult], live: asyncio.Semaphore
	) -> None:
		async with live:
			await self._run_agent(agent, results[agent.id])

	async def _run_agent(self, agent: Agent, result: AgentResult) -> None:
		_agents.set_current_agent(agent.id, self.states)
		states = self.states
		result.t_start = time.time()
		states.enter(agent.id, AgentState.WORKING)
		output: Any = None
		with _telemetry.tracer.start_as_current_span(
			f"{_telemetry.INVOKE_AGENT} {agent.id}",
			attributes={
				_telemetry.GEN_AI_OPERATION_NAME: _telemetry.INVOKE_AGENT,
				_telemetry.GEN_AI_AGENT_ID: agent.id,
			},
		) as span:
			try:
				step = await self.runner.start(agent)
				while isinstance(step, Suspended):
					result.suspensions += 1
					done = await self._wait(agent, step)
					step = await self.runner.resume(agent, done)
				if isinstance(step, Interrupted):
					result.status = "interrupted"
					result.interrupts = step.interrupts
				else:
					result.status = "done"
					output = step.output
			except Exception as exc:  # one agent's failure must not stop the others
				result.status = "failed"
				result.error = f"{type(exc).__name__}: {exc}"[:500]
				span.set_attribute(_telemetry.ERROR_TYPE, type(exc).__qualname__)
				log.debug(
					"agent %s failed: %s",
					agent.id,
					"".join(traceback.format_exception(exc)[-4:]),
				)
			finally:
				result.t_end = time.time()
				states.enter(agent.id, AgentState.DONE)
		if self.on_done is not None:
			self.on_done(result, output)

	async def _wait(self, agent: Agent, step: Suspended) -> dict[str, Any]:
		self.states.enter(agent.id, AgentState.TOOL_WAIT)
		_telemetry.emit_suspend(agent.id, len(step.waits))
		futures = []
		for _, key in step.waits:
			future = _agents.pending(key)
			if future is None:
				raise RuntimeError(
					f"agent {agent.id} waits on task {key!r}, which this process "
					"did not submit"
				)
			futures.append(future)
		try:
			await asyncio.wait(futures)
		finally:
			self.states.enter(agent.id, AgentState.WORKING)
		return {rid: {"flowgentic_done": key} for rid, key in step.waits}

	# --- the collector -----------------------------------------------------------

	def _collector_start(self) -> tuple[int, int, int] | None:
		if self.collector is None:
			return None
		saved = gc.get_threshold()
		gc.collect()
		gc.set_threshold(self.collector.threshold, saved[1], saved[2])
		return saved

	def _collector_started(self, t0: float) -> dict:
		if self.collector is None or not self.collector.freeze:
			return {}
		t = time.perf_counter()
		gc.collect()
		gc.freeze()
		return {
			"at_s": round(time.monotonic() - t0, 3),
			"call_s": round(time.perf_counter() - t, 4),
			"frozen": gc.get_freeze_count(),
		}

	def _collector_end(self, saved: tuple[int, int, int] | None) -> None:
		if saved is None:
			return
		if self.collector is not None and self.collector.freeze:
			gc.unfreeze()
		gc.set_threshold(*saved)
