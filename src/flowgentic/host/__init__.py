"""The agent host: runs many agents in one process, at HPC scale.

An ``AgentHost`` runs agents of any framework through a ``Runner``, the small
adapter each framework provides (``flowgentic.langgraph.GraphRunner`` for
LangGraph). It owns what makes thousands of agents in one Python process work:

- **The collector.** CPython's cyclic collector, at its default settings,
  spends most of a busy agent process's time scanning the agents' live
  objects. The host raises its first threshold while it runs, and freezes the
  objects that exist once agents have started, so later collections skip them.
  It restores the threshold when ``run`` returns, and unfreezes when the last
  host that froze ends.
- **A cap on live agents.** At most ``max_live_agents`` agents are live at
  once, suspended ones included; more are accepted, and each starts as soon as
  a live one ends. The cap bounds the event loop's lag and the collector's
  pauses.
- **Staggered starts.** The first wave, up to the cap, starts in batches, so
  its agents do not all reach their first model call or their first suspension
  at the same moment. Later agents are not staggered.
- **Suspension.** When an agent's runner reports that the agent waits on tasks,
  the host holds no state of the agent's, only its id and the tasks' futures:
  the framework keeps the state in its checkpoint. The host awaits the tasks and
  resumes the agent. Whatever submits a task registers its future under the
  task's key with ``register_pending``, and the runner names those keys.
- **Accounting.** ``AgentStates`` counts the agents in each state, from which a
  campaign reports how many agents are working, waiting, and making model
  requests. One agent's failure never stops the others. When ``run`` is
  cancelled, each live agent ends as ``cancelled``.

A runner of the application's own, such as one whose code, not a model, picks
each step, uses what Flowgentic's runners use: ``current_agent()``, the agent
the current coroutine works for, which an inline tool reads too;
``activity(state)``, which counts that agent in a state for a block; ``wait``,
for the tasks of a ``ToolRuntime``; and ``register_pending``.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from opentelemetry.trace import Status, StatusCode

from flowgentic import _agents, _telemetry
from flowgentic._agents import (
	AgentState,
	AgentStates,
	activity,
	current_agent,
	register_pending,
	wait,
)
from flowgentic.host._probes import CollectorPauses, LoopLag

__all__ = [
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
	"activity",
	"current_agent",
	"register_pending",
	"wait",
]

log = logging.getLogger(__name__)

# Suspensions in a row on the same tasks, all already ended, after which the
# host fails the agent: its runner makes no progress.
_STALLS = 100


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
	the collector's reach. When the last host that froze ends, every frozen
	object is moved back, those frozen before it included (CPython 3.12 parks
	some of its own objects there): CPython cannot thaw only part of them, and
	reference cycles left frozen would never be freed. An application that
	keeps objects of its own frozen passes ``freeze=False``. The defaults were
	measured on CPython 3.12. ``threshold`` must be at least 1: CPython takes 0
	to turn automatic collection off, and collects at nearly every allocation
	below 0.
	"""

	threshold: int = 100_000
	freeze: bool = True

	def __post_init__(self) -> None:
		if self.threshold < 1:
			raise ValueError("a collector's threshold must be at least 1")


@dataclass(frozen=True, slots=True)
class Stagger:
	"""Start the first wave of agents, up to the host's cap, ``batch`` at a
	time, ``interval_s`` seconds apart."""

	batch: int = 100
	interval_s: float = 0.5

	def __post_init__(self) -> None:
		if self.batch < 1 or self.interval_s < 0:
			raise ValueError("a stagger needs batch >= 1 and interval_s >= 0")


@dataclass(slots=True)
class AgentResult:
	"""How one agent's run ended. Its output goes to ``on_done``, not here.

	``error`` is the agent's own error. ``callback_error`` is the error
	``on_done`` raised for this agent, which also makes the agent ``failed``.
	"""

	id: str
	status: str = "pending"  # done, failed, interrupted or cancelled
	error: str | None = None
	t_start: float = 0.0
	t_end: float = 0.0
	suspensions: int = 0
	interrupts: tuple[Any, ...] = ()
	callback_error: str | None = None


@dataclass(slots=True)
class HostReport:
	"""What a run did, and what it cost the process."""

	agents: list[AgentResult]
	states: dict
	loop_lag: dict
	collector: dict
	wall_s: float
	errors: dict[str, int] = field(default_factory=dict)
	outcomes: dict[str, int] = field(default_factory=dict)

	@property
	def finished(self) -> int:
		if self.outcomes:
			return self.outcomes.get("done", 0)
		return sum(1 for a in self.agents if a.status == "done")


# CPython shares its thresholds and permanent generation across event loops.
_collector_lock = threading.RLock()
_thresholds: dict[object, int] = {}
_original_thresholds: tuple[int, int, int]
_freeze_owners: set[object] = set()
_frozen_before = 0  # the permanent generation's size when the first owner froze


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
		if len({a.id for a in work}) != len(work):
			raise ValueError("agent ids must be unique within a run")
		self.states._begin_run(a.id for a in work)
		_agents.begin_host_run()
		try:
			return await self._run(work, len(work), indexed=False)
		finally:
			_agents.end_host_run()
			self.states._end_run()

	async def run_indexed(
		self,
		indices: range,
		input_for: Callable[[int], Any],
		*,
		id_prefix: str = "",
	) -> HostReport:
		"""Run a range of unique IDs with inputs built on admission.

		The agent ID is the prefix followed by its decimal index. The report
		keeps aggregate outcomes; on_done receives each individual result.
		Only admitted agents have a per-ID state entry in this mode.
		"""
		if not isinstance(indices, range):
			raise TypeError("indices must be a range")
		count = len(indices)
		self.states._begin_run((), pending=count)
		_agents.begin_host_run()
		try:
			agents = (Agent(f"{id_prefix}{i}", input_for(i)) for i in indices)
			return await self._run(agents, count, indexed=True)
		finally:
			_agents.end_host_run()
			self.states._end_run()

	async def _run(
		self, work: Iterable[Agent], count: int, *, indexed: bool
	) -> HostReport:
		results: list[AgentResult] = []
		outcomes: Counter[str] = Counter()
		errors: Counter[str] = Counter()
		tasks: set[asyncio.Task] = set()
		completed = asyncio.Queue()

		def collect(task: asyncio.Task) -> None:
			tasks.remove(task)
			if task.cancelled():  # cancelled from outside, not by this host
				outcomes["cancelled"] += 1
				return
			result = task.result()
			outcomes[result.status] += 1
			error = result.error or result.callback_error
			if result.status == "failed" and error:
				errors[error.split(":", 1)[0]] += 1

		lag, pauses = LoopLag(), CollectorPauses()
		saved = self._collector_start()
		freeze: dict = {}
		lag.start()
		pauses.start()
		t0 = time.monotonic()
		try:
			first = min(count, self.max_live_agents)
			inputs = iter(work)
			for i in range(count):
				# Only the first wave is staggered; later agents fill free slots.
				if i and i < first and i % self.stagger.batch == 0:
					await asyncio.sleep(self.stagger.interval_s)
				if len(tasks) >= self.max_live_agents:
					collect(await completed.get())
				while not completed.empty():
					collect(completed.get_nowait())
				agent = next(inputs)
				result = AgentResult(agent.id)
				if indexed:
					self.states._admit(agent.id)
				else:
					results.append(result)
				task = asyncio.create_task(self._run_agent(agent, result, indexed))
				tasks.add(task)
				task.add_done_callback(completed.put_nowait)
				if i + 1 == first:
					await asyncio.sleep(0)
					freeze = self._collector_started(saved, t0)
			while tasks:
				collect(await completed.get())
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
		return HostReport(
			agents=results,
			outcomes=dict(outcomes),
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
			errors=dict(errors),
		)

	async def _run_agent(
		self, agent: Agent, result: AgentResult, indexed: bool
	) -> AgentResult:
		_agents.set_current_agent(agent.id, self.states)
		states = self.states
		result.t_start = time.time()
		states.enter(agent.id, AgentState.WORKING)
		output: Any = None
		cancelled: asyncio.CancelledError | None = None
		# Named for the operation only: the agent's id is in gen_ai.agent.id, and
		# a name per agent would make as many span names as agents.
		with _telemetry.tracer.start_as_current_span(
			_telemetry.INVOKE_AGENT,
			attributes={
				_telemetry.GEN_AI_OPERATION_NAME: _telemetry.INVOKE_AGENT,
				_telemetry.GEN_AI_AGENT_ID: agent.id,
			},
		) as span:
			try:
				step = await self.runner.start(agent)
				stalled, last = 0, None
				while isinstance(step, Suspended):
					result.suspensions += 1
					done, waited = await self._wait(agent, step)
					# A runner that suspends again on the same tasks, all ended,
					# makes no progress: resumed at once each time, it would
					# keep the event loop busy for good.
					keys = tuple(key for _, key in step.waits)
					stalled = 0 if waited or keys != last else stalled + 1
					last = keys
					if stalled >= _STALLS:
						raise RuntimeError(
							f"agent {agent.id} suspended {_STALLS} times in a row "
							f"on tasks that had already ended ({', '.join(keys)}): "
							"its runner makes no progress"
						)
					step = await self.runner.resume(agent, done)
				if isinstance(step, Interrupted):
					result.status = "interrupted"
					result.interrupts = step.interrupts
				else:
					result.status = "done"
					output = step.output
			except (Exception, asyncio.CancelledError) as exc:
				task = asyncio.current_task()
				if isinstance(exc, asyncio.CancelledError) and task.cancelling():
					# This task was cancelled, as the host does when it stops:
					# end the agent here, then let the task end cancelled.
					result.status = "cancelled"
					cancelled = exc
					span.set_status(Status(StatusCode.ERROR, "cancelled"))
				else:
					# Any other error, a CancelledError raised inside the runner
					# included, ends this agent only.
					result.status = "failed"
					result.error = f"{type(exc).__name__}: {exc}"[:500]
					log.debug("agent %s failed", agent.id, exc_info=True)
					span.record_exception(exc)
					span.set_status(Status(StatusCode.ERROR, result.error))
				span.set_attribute(_telemetry.ERROR_TYPE, type(exc).__qualname__)
			finally:
				result.t_end = time.time()
				_agents.release_pending()
				states.enter(agent.id, AgentState.DONE)
		if self.on_done is not None:
			try:
				self.on_done(result, output)
			except Exception as exc:
				result.status = "failed"
				result.callback_error = f"{type(exc).__name__}: {exc}"[:500]

		if indexed:
			states._retire(agent.id)
		if cancelled is not None:
			raise cancelled
		return result

	async def _wait(self, agent: Agent, step: Suspended) -> tuple[dict[str, Any], bool]:
		"""Wait for the step's tasks; their results for the runner, and whether
		any had not ended yet."""
		self.states.enter(agent.id, AgentState.TOOL_WAIT)
		_telemetry.emit_suspend(agent.id, len(step.waits))
		futures = []
		for _, key in step.waits:
			future = _agents.take_pending(key)
			if future is None:
				raise RuntimeError(
					f"agent {agent.id} waits on task {key!r}, which this process "
					"did not submit"
				)
			futures.append(future)
		waited = not all(f.done() for f in futures)
		try:
			# A stopped host withdraws the tasks that their order has not
			# admitted yet, when no other call waits for them.
			await _agents.wait(futures)
		finally:
			self.states.enter(agent.id, AgentState.WORKING)
		return {rid: {"flowgentic_done": key} for rid, key in step.waits}, waited

	# --- the collector -----------------------------------------------------------

	def _collector_start(self) -> object | None:
		global _original_thresholds
		if self.collector is None:
			return None
		with _collector_lock:
			if not _thresholds:
				_original_thresholds = gc.get_threshold()
			gc.collect()
			gc.set_threshold(self.collector.threshold, *_original_thresholds[1:])
			token = object()
			_thresholds[token] = self.collector.threshold
			return token

	def _collector_started(self, token: object | None, t0: float) -> dict:
		global _frozen_before
		if self.collector is None or not self.collector.freeze or token is None:
			return {}
		with _collector_lock:
			t = time.perf_counter()
			if not _freeze_owners:
				# Freezing adds to what is frozen already, such as the objects
				# CPython 3.12 moves there at each full collection.
				_frozen_before = gc.get_freeze_count()
				gc.collect()
				gc.freeze()
			_freeze_owners.add(token)
			return {
				"at_s": round(time.monotonic() - t0, 3),
				"call_s": round(time.perf_counter() - t, 4),
				"frozen_before": _frozen_before,
				"frozen": gc.get_freeze_count(),
			}

	def _collector_end(self, token: object | None) -> None:
		if token is None:
			return
		with _collector_lock:
			if token in _freeze_owners:
				_freeze_owners.remove(token)
				# This thaws what was frozen before the host too: CPython cannot
				# thaw only what the host froze, and leaving it all frozen would
				# keep the agents' reference cycles for the life of the process.
				if not _freeze_owners:
					gc.unfreeze()
			del _thresholds[token]
			threshold = next(reversed(_thresholds.values()), _original_thresholds[0])
			gc.set_threshold(threshold, *_original_thresholds[1:])
