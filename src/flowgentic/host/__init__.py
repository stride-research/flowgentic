"""The agent host: runs many agents in one process, at HPC scale.

An ``AgentHost`` runs agents of any framework through a ``Runner``, the small
adapter each framework provides (``flowgentic.langgraph.GraphRunner`` for
LangGraph). It runs a fixed set of agents with ``run`` or ``run_indexed``, or
takes agents one at a time, as they arrive, between ``start`` and ``stop``,
or in an ``async with host:`` block: ``submit`` gives it one more and returns
a future of how it ended. It owns
what makes thousands of agents in one Python process work:

- **The collector.** CPython's cyclic collector, at its default settings,
  spends most of a busy agent process's time scanning the agents' live
  objects. The host raises its first threshold while it runs, and freezes the
  objects that exist once agents have started, so later collections skip them.
  It restores the threshold when ``run`` returns or ``stop`` ends the host, and
  unfreezes when the last host that froze ends.
- **A cap on live agents.** At most ``max_live_agents`` agents are live at
  once, suspended ones included; more are accepted, and each starts as soon as
  a live one ends, in the order they came. The cap bounds the event loop's lag
  and the collector's pauses. ``max_queued`` bounds the agents ``submit``
  accepts beyond it.
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
  cancelled, or ``stop(drain=False)`` called, each live agent ends as
  ``cancelled``.

A runner of the application's own, such as one whose code, not a model, picks
each step, uses what Flowgentic's runners use: ``current_agent()``, the agent
the current coroutine works for, which an inline tool reads too;
``activity(state)``, which counts that agent in a state for a block; ``wait``,
for the tasks of a ``ToolRuntime``; and ``register_pending``.
"""

from __future__ import annotations

import asyncio
import contextvars
import gc
import logging
import threading
import time
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol

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

if TYPE_CHECKING:
	from types import TracebackType

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
	"QueueFull",
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


class QueueFull(Exception):  # noqa: N818  # as asyncio.QueueFull
	"""``AgentHost.submit`` refused an agent: the host holds
	``max_live_agents + max_queued`` agents already, live or waiting to start.
	A server answers "busy, retry later" rather than pile the work up."""


class _Submitted:
	"""An agent given to ``submit``: its future and the context it runs in, and
	its task and result once it starts. ``agent`` is ``None`` once it is
	withdrawn."""

	__slots__ = ("agent", "context", "future", "result", "task")

	def __init__(
		self, agent: Agent, future: asyncio.Future, context: contextvars.Context
	) -> None:
		self.agent: Agent | None = agent
		self.future = future
		self.context: contextvars.Context | None = context
		self.result: AgentResult | None = None
		self.task: asyncio.Task | None = None


# Where a period's agents come from: run's list, run_indexed's range, submit.
_RUN, _INDEXED, _SERVED = "run", "indexed", "served"


class _Session:
	"""One period of a host's work: from ``start``, or ``run``, to its end."""

	__slots__ = (
		"admitted",
		"closed",
		"collect",
		"dispatcher",
		"dispatching",
		"draining",
		"ended",
		"errors",
		"finished",
		"freeze",
		"ids",
		"lag",
		"last_admit",
		"left",
		"loop",
		"mode",
		"outcomes",
		"owner",
		"pauses",
		"queue",
		"report",
		"results",
		"source",
		"stopping",
		"t0",
		"tasks",
		"token",
		"wake",
	)

	def __init__(
		self,
		loop: asyncio.AbstractEventLoop,
		mode: str,
		source: Iterator[Agent] | None,
		count: int,
	) -> None:
		self.loop = loop
		self.mode = mode
		# run's and run_indexed's agents, taken as slots free, and how many are left
		self.source = source
		self.left = count
		# Submitted agents waiting to start, by id, in the order they came: one
		# withdrawn leaves at once. The ids of the agents live or waiting.
		self.queue: OrderedDict[str, _Submitted] = OrderedDict()
		self.ids: set[str] = set()
		# The live agents' tasks, each with its result, or its submission.
		self.tasks: dict[asyncio.Task, AgentResult | _Submitted] = {}
		self.results: list[AgentResult] = []  # run's, one per agent started
		self.outcomes: Counter[str] = Counter()
		self.errors: Counter[str] = Counter()
		# Set when a slot frees, an agent comes, or the host closes.
		self.wake = asyncio.Event()
		self.closed = mode != _SERVED  # submit refuses agents
		self.stopping = False
		# stop(drain=True) waits: submit takes agents from this period's agents
		# still, so that one that delegates to another ends as it would have.
		self.draining = False
		self.dispatching = True
		self.ended = False
		# Every agent has ended, or the error that stops the host.
		self.finished: asyncio.Future = loop.create_future()
		self.report: asyncio.Future = loop.create_future()  # for a second stop
		self.admitted = 0
		self.last_admit = 0.0
		self.freeze: dict | None = None
		self.token: object | None = None
		self.lag = LoopLag()
		self.pauses = CollectorPauses()
		self.t0 = 0.0
		self.dispatcher: asyncio.Task | None = None
		self.collect: Callable[[asyncio.Task], None] | None = None
		# What this period's agents carry, so that the host knows its own.
		self.owner = object()

	def waiting(self) -> bool:
		"""Whether an agent waits to start."""
		return bool(self.queue or self.left)


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
		max_queued: int | None = None,
	) -> None:
		if max_live_agents < 1:
			raise ValueError("max_live_agents must be at least 1")
		if max_queued is not None and max_queued < 0:
			raise ValueError("max_queued must be None or at least 0")
		self.runner = runner
		self.max_live_agents = max_live_agents
		self.stagger = stagger
		self.collector = collector
		self.on_done = on_done
		# the application may pass its own, to sample it while agents run
		self.states = states if states is not None else AgentStates()
		self.max_queued = max_queued
		self._session: _Session | None = None

	async def run(self, agents: Iterable[Agent]) -> HostReport:
		"""Run every agent to its end, and report."""
		work = list(agents)
		if len({a.id for a in work}) != len(work):
			raise ValueError("agent ids must be unique within a run")
		session = self._open(_RUN, iter(work), len(work), (a.id for a in work))
		return await self._close(session, drain=True)

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
		agents = (Agent(f"{id_prefix}{i}", input_for(i)) for i in indices)
		session = self._open(_INDEXED, agents, len(indices))
		return await self._close(session, drain=True)

	# --- agents on demand --------------------------------------------------------

	async def start(self) -> None:
		"""Start taking agents one at a time, with ``submit``, until ``stop``.

		The host tunes the collector, starts its probes, and begins its
		accounting period, as ``run`` does. It runs one period at a time:
		``start``, ``run`` or ``run_indexed`` while it runs raise
		``RuntimeError``. ``async with host:`` starts it and stops it at the
		block's end, so an error between the two cannot leave the collector
		tuned."""
		self._open(_SERVED, None, 0)

	async def __aenter__(self) -> AgentHost:
		"""``start`` the host."""
		await self.start()
		return self

	async def __aexit__(
		self,
		exc_type: type[BaseException] | None,
		exc: BaseException | None,
		tb: TracebackType | None,
	) -> None:
		"""``stop`` the host, if the block has not: draining it, or, when the
		block raised, cancelling its agents (``stop(drain=False)``)."""
		s = self._session
		if s is not None and s.mode == _SERVED:
			await self.stop(drain=exc_type is None)

	def submit(self, agent: Agent) -> asyncio.Future[tuple[AgentResult, Any]]:
		"""Give the started host one more agent, and return at once.

		The future resolves to ``(result, output)``, what ``on_done``
		receives, when the agent ends, whether it finished, failed, was
		interrupted or was cancelled: an agent's error is in ``result.error``,
		never raised. The agent starts as soon as a slot is free, after the
		agents submitted before it. It runs in a copy of the caller's context,
		as a task ``asyncio.create_task`` makes there.

		Cancelling the future cancels the agent: one still waiting never
		starts, and one live ends as ``cancelled``. Call ``submit`` on the
		host's event loop. It raises ``RuntimeError`` before ``start`` and once
		``stop`` is called, ``ValueError`` if an agent with the same id is live
		or waiting (an id may be used again once its agent has ended), and
		``QueueFull`` when ``max_queued`` agents already wait for a slot.
		While ``stop()`` drains, the host's own agents may still submit, so
		that an agent that delegates to another ends as it would have."""
		s = self._session
		if s is None:
			raise RuntimeError("this host is not running: await host.start() first")
		if s.closed:
			if s.mode != _SERVED:
				raise RuntimeError(
					"this host runs a fixed set of agents (run or run_indexed)"
				)
			if not (s.draining and _agents.agent_of(s.owner) is not None):
				raise RuntimeError(
					"this host is stopping"
					if s.stopping
					else "this host's own task has ended: stop the host"
				)
		loop = asyncio.get_running_loop()
		if loop is not s.loop:
			raise RuntimeError("submit agents on the event loop the host runs on")
		if agent.id in s.ids:
			raise ValueError(
				f"agent {agent.id!r} is live or waiting in this host already"
			)
		if (
			self.max_queued is not None
			and len(s.queue) + len(s.tasks) >= self.max_live_agents + self.max_queued
		):
			raise QueueFull(
				f"{len(s.tasks)} agents live and {len(s.queue)} waiting: the host "
				f"holds at most max_live_agents + max_queued = "
				f"{self.max_live_agents + self.max_queued}"
			)
		future = loop.create_future()
		entry = _Submitted(agent, future, contextvars.copy_context())
		future.add_done_callback(partial(self._cancelled, s, entry))
		s.queue[agent.id] = entry
		s.ids.add(agent.id)
		self.states.add(agent.id)
		s.wake.set()
		return future

	async def stop(self, *, drain: bool = True) -> HostReport:
		"""Stop taking agents, end the host, and report.

		With ``drain``, every agent live or waiting runs to its end first; the
		host's own agents may still submit meanwhile. Without, each live agent
		ends as ``cancelled``, as when ``run`` is cancelled, and each waiting
		one never starts: its future resolves to a ``cancelled`` result with no
		start time, ``on_done`` does not receive it, and ``outcomes`` counts
		it as ``cancelled``. ``stop(drain=False)`` while a ``stop()`` drains
		cancels what is left; both return the same report. Cancelling ``stop``
		ends the host as cancelling ``run`` does.

		``stop`` ends what ``start`` began: while ``run`` or ``run_indexed``
		runs, it raises ``RuntimeError``, as it does in one of the host's own
		agents, which would wait for its own end."""
		s = self._session
		if s is None:
			raise RuntimeError("this host is not running")
		if s.mode != _SERVED:
			raise RuntimeError(
				"this host runs a fixed set of agents: run returns once they have "
				"ended, and cancelling it cancels them"
			)
		agent = _agents.agent_of(s.owner)
		if agent is not None and not agent.finished:
			# In one of its agents, or a task one started: it would wait for
			# the agent's end, and the agent, maybe, for it.
			raise RuntimeError("an agent cannot stop the host it runs in")
		if s.stopping:
			if not drain:
				self._cancel_all(s)
			return await asyncio.shield(s.report)
		return await self._close(s, drain)

	# --- a period of work: from start, or run, to the report -------------------------

	def _open(
		self,
		mode: str,
		source: Iterator[Agent] | None,
		count: int,
		ids: Iterable[str] = (),
	) -> _Session:
		if self._session is not None:
			raise RuntimeError("this host is running already")
		loop = asyncio.get_running_loop()
		if mode == _INDEXED:
			self.states._begin_run((), pending=count)
		else:
			self.states._begin_run(ids)
		_agents.begin_host_run()
		s = _Session(loop, mode, source, count)
		s.collect = partial(self._collect, s)
		s.token = self._collector_start()
		s.lag.start()
		s.pauses.start()
		s.t0 = time.monotonic()
		s.dispatcher = loop.create_task(self._dispatch(s), name="flowgentic-host")
		# A callback, not the task's own finally: a task cancelled before its
		# first step never runs its body.
		s.dispatcher.add_done_callback(partial(self._dispatched, s))
		self._session = s
		return s

	async def _close(self, s: _Session, drain: bool) -> HostReport:
		s.stopping = s.closed = True
		# Only while the host's own task runs, to start what its agents submit.
		s.draining = drain and s.mode == _SERVED and s.dispatching
		s.wake.set()
		if not drain:
			self._cancel_all(s)
		try:
			await s.finished
		except BaseException:
			# Cancelled, or an error that stops the host: end every agent and the
			# host, then raise.
			try:
				self._cancel_all(s)
				await asyncio.gather(s.dispatcher, *s.tasks, return_exceptions=True)
			finally:
				self._end(s)
			raise
		return self._end(s)

	async def _dispatch(self, s: _Session) -> None:
		"""Start the agents in the order they came, as slots free; stagger the
		first wave, and freeze once it has started."""
		cap = self.max_live_agents
		batch, interval = self.stagger.batch, self.stagger.interval_s
		loop = s.loop
		try:
			while True:
				while len(s.tasks) >= cap or not s.waiting():
					# While a stop drains, a live agent may still submit one.
					if s.closed and not s.waiting() and not (s.draining and s.tasks):
						return
					s.wake.clear()
					await s.wake.wait()
				i = s.admitted
				if i and i < cap and i % batch == 0:
					# The first wave starts batch at a time, interval_s apart.
					await asyncio.sleep(max(0.0, s.last_admit + interval - loop.time()))
					if not s.waiting():
						continue
				if s.left:
					s.left -= 1
					agent = next(s.source)
					entry = context = None
				else:
					_, entry = s.queue.popitem(last=False)
					if entry.future.cancelled():  # its callback has not run yet
						self._withdraw(s, entry)
						continue
					agent, context = entry.agent, entry.context
					entry.context = None
				result = AgentResult(agent.id)
				if s.mode == _RUN:
					s.results.append(result)
				elif s.mode == _INDEXED:
					self.states._admit(agent.id)
				task = loop.create_task(
					self._run_agent(s, agent, result), context=context
				)
				if entry is None:
					s.tasks[task] = result
				else:
					entry.task, entry.result = task, result
					s.tasks[task] = entry
				task.add_done_callback(s.collect)
				s.admitted += 1
				s.last_admit = loop.time()
				if s.freeze is None and (s.admitted >= cap or not s.waiting()):
					# The first wave has started: freeze what is alive now.
					await asyncio.sleep(0)
					s.freeze = self._collector_started(s.token, s.t0)
		except asyncio.CancelledError:
			raise
		except BaseException as exc:  # as run_indexed's input_for may raise
			self._fail(s, exc)

	def _dispatched(self, s: _Session, _task: asyncio.Task) -> None:
		"""The host's own task has ended: no agent will start any more. If it
		ended early, as when cancelled from outside, the host closes and
		withdraws the waiting agents, so that their futures end; the live ones
		run on to their ends."""
		s.dispatching = False
		s.draining = False
		if not s.ended and (not s.closed or s.waiting()):
			log.warning(
				"the agent host's own task ended before the host was stopped: "
				"it starts no more agents"
			)
			s.closed = True
			s.left = 0
			self._withdraw_waiting(s)
		if not s.tasks and not s.finished.done():
			s.finished.set_result(None)

	def _collect(self, s: _Session, task: asyncio.Task) -> None:
		"""An agent's task has ended: count the agent, free its slot and its id,
		then resolve its future, so that its caller finds both free."""
		if s.ended:
			return
		# One callback for every agent, not an object each for the collector to
		# track: a run's heap is large, and its scans grow with what is tracked.
		held = s.tasks.pop(task)
		if type(held) is _Submitted:
			result, future = held.result, held.future
		else:
			result, future = held, None
		agent_id = result.id
		output: Any = None
		exc: BaseException | None = None
		if task.cancelled():  # by this host, or from outside
			s.outcomes["cancelled"] += 1
			if result.status == "pending" and s.mode != _RUN:
				# Cancelled before its first step: it never started. Agents of
				# run stay pending, as when run is cancelled.
				result.status = "cancelled"
				self.states.enter(agent_id, AgentState.DONE)
		elif (exc := task.exception()) is not None:
			# Neither an Exception nor the host's cancellation: it stops the host.
			self._fail(s, exc)
		else:
			output = task.result()
			s.outcomes[result.status] += 1
			error = result.error or result.callback_error
			if result.status == "failed" and error:
				s.errors[error.split(":", 1)[0]] += 1
		if s.mode != _RUN:
			# Nothing is kept of an agent that ended; its id may be used again.
			self.states._retire(agent_id)
			s.ids.discard(agent_id)
		s.wake.set()
		if future is not None and not future.done():
			if exc is None:
				future.set_result((result, output))
			else:
				future.set_exception(exc)
		if not s.tasks and not s.dispatching and not s.finished.done():
			s.finished.set_result(None)

	def _fail(self, s: _Session, exc: BaseException) -> None:
		if not s.finished.done():
			s.finished.set_exception(exc)
		self._cancel_all(s)

	def _cancel_all(self, s: _Session) -> None:
		"""Cancel the live agents and withdraw the waiting ones. Agents of
		``run`` not started yet stay pending, as when ``run`` is cancelled."""
		s.closed = True
		s.draining = False
		s.left = 0
		if s.dispatcher is not None:
			s.dispatcher.cancel()
		self._withdraw_waiting(s)
		for task in s.tasks:
			task.cancel()
		s.wake.set()

	def _withdraw_waiting(self, s: _Session) -> None:
		while s.queue:
			_, entry = s.queue.popitem(last=False)
			self._withdraw(s, entry)

	def _withdraw(self, s: _Session, entry: _Submitted) -> None:
		"""End a submitted agent that never started, as ``cancelled``: it
		leaves the queue at once, with its future and its context."""
		agent = entry.agent
		entry.agent = entry.context = None
		if s.queue.get(agent.id) is entry:
			del s.queue[agent.id]
		s.ids.discard(agent.id)
		self.states.enter(agent.id, AgentState.DONE)
		self.states._retire(agent.id)
		s.outcomes["cancelled"] += 1
		if not entry.future.done():
			entry.future.set_result((AgentResult(agent.id, status="cancelled"), None))

	def _cancelled(
		self, s: _Session, entry: _Submitted, future: asyncio.Future
	) -> None:
		"""A submitted agent's future is done: if its caller cancelled it, cancel
		the agent. The host resolves every other."""
		if not future.cancelled() or s.ended:
			return
		if entry.task is not None:
			entry.task.cancel()
		elif entry.agent is not None:
			self._withdraw(s, entry)

	def _end(self, s: _Session) -> HostReport:
		s.ended = True
		wall = time.monotonic() - s.t0
		try:
			s.lag.stop()
			s.pauses.stop()
			self._collector_end(s.token)
			report = HostReport(
				agents=s.results,
				outcomes=dict(s.outcomes),
				states=self.states.summary(),
				loop_lag=s.lag.summary(),
				collector={
					**s.pauses.summary(),
					"settings": (
						None
						if self.collector is None
						else {
							"threshold": self.collector.threshold,
							"freeze": self.collector.freeze,
						}
					),
					"freeze": s.freeze or {},
				},
				wall_s=round(wall, 3),
				errors=dict(s.errors),
			)
		finally:
			_agents.end_host_run()
			self.states._end_run()
			self._session = None
		if not s.report.done():
			s.report.set_result(report)
		s.collect = None  # it holds the session: a cycle the collector need not break
		return report

	async def _run_agent(
		self,
		s: _Session,
		agent: Agent,
		result: AgentResult,
	) -> Any:  # noqa: ANN401  # the agent's output, whatever its framework returns
		"""Run one agent to its end; its output. ``_collect`` counts it."""
		_agents.set_current_agent(agent.id, self.states, s.owner)
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

		if cancelled is not None:
			raise cancelled
		return output

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
