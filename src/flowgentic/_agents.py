"""Which agent a coroutine works for, what it is doing, and the tasks it waits on.

The agent host sets a context variable in each agent's task. asyncio copies the
context into the tasks a framework creates for the agent's nodes and tool
calls, so the fleet and the tools reach the agent through it, without being
passed anything. Outside a host they find no agent and record nothing, so they
work without one.

An agent may run several tool calls at once. Each call opens an activity, and
the agent's state is the most telling of its open activities, so concurrent
calls never undo each other's state.

The registry of pending tasks is how a suspended agent is resumed: a task tool
registers the future of each task it submits under the task's key, and the host
awaits that future while the agent is suspended.
"""

from __future__ import annotations

import asyncio
import threading
import time
import weakref
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import Context, ContextVar
from enum import IntEnum

from opentelemetry import context as otel_context


class AgentState(IntEnum):
	"""What an agent is doing."""

	PENDING = 0
	"""Not started."""
	WORKING = 1
	"""In the agent process: the framework's own steps and inline tools."""
	TOOL_CALL = 2
	"""A call running in the tool service, such as an MCP tool."""
	MODEL_QUEUED = 3
	"""Waiting for its model replica to admit a request."""
	MODEL_REQUEST = 4
	"""A request in flight at a model server."""
	TOOL_WAIT = 5
	"""Waiting for a task, queued or running, suspended or not."""
	DONE = 6
	"""Finished, failed, interrupted or cancelled."""


CATEGORY: dict[AgentState, str] = {
	AgentState.WORKING: "working",
	AgentState.TOOL_CALL: "working",
	AgentState.MODEL_QUEUED: "waiting",
	AgentState.TOOL_WAIT: "waiting",
	AgentState.MODEL_REQUEST: "model_request",
}
"""The three categories reported for a campaign: working, waiting, and making a
model request."""

# With several activities open, the agent's state is the first of these.
_PRECEDENCE = (
	AgentState.TOOL_CALL,
	AgentState.MODEL_REQUEST,
	AgentState.MODEL_QUEUED,
	AgentState.TOOL_WAIT,
)

_N = len(AgentState)

# Every AgentStates in the process, for the telemetry's observable gauges.
_all_states: weakref.WeakSet[AgentStates] = weakref.WeakSet()


class AgentStates:
	"""How many agents are in each state, and the agent-seconds spent in each.

	``enter`` is called on the event loop and costs O(1). ``counts``,
	``agent_seconds`` and ``by_category`` may be read from another thread, such
	as a sampler or a metrics exporter.
	"""

	def __init__(self, agents: Iterable[str] = ()) -> None:
		self._state: dict[str, AgentState] = {}
		self._counts = [0] * _N
		self._seconds = [0.0] * _N
		self._t = time.monotonic()
		self._lock = threading.Lock()
		self._running = False
		for agent in agents:
			self.add(agent)
		_all_states.add(self)

	def _begin_run(self, ids: Iterable[str], *, pending: int | None = None) -> None:
		with self._lock:
			if self._running:
				raise RuntimeError("these agent states already track an active run")
			self._state = dict.fromkeys(ids, AgentState.PENDING)
			self._counts = [0] * _N
			self._counts[AgentState.PENDING] = (
				len(self._state) if pending is None else pending
			)
			self._seconds = [0.0] * _N
			self._t = time.monotonic()
			self._running = True

	def _end_run(self) -> None:
		# Agents that never started, in a run that failed or was cancelled,
		# stay pending.
		with self._lock:
			self._advance(time.monotonic())
			self._t = None
			self._running = False

	def _admit(self, agent: str) -> None:
		# The indexed run counted all pending agents at startup.
		with self._lock:
			self._state[agent] = AgentState.PENDING

	def _retire(self, agent: str) -> None:
		with self._lock:
			del self._state[agent]

	def add(self, agent: str) -> None:
		"""Count a new agent, as pending."""
		with self._lock:
			if agent in self._state:
				return
			self._advance(time.monotonic())
			self._state[agent] = AgentState.PENDING
			self._counts[AgentState.PENDING] += 1

	def enter(self, agent: str, state: AgentState) -> AgentState:
		"""Move ``agent`` to ``state``; return the state it was in."""
		old = self._state[agent]
		if old != state:
			with self._lock:
				self._advance(time.monotonic())
				self._counts[old] -= 1
				self._counts[state] += 1
				self._state[agent] = state
		return old

	def state(self, agent: str) -> AgentState:
		return self._state[agent]

	def _advance(self, now: float) -> None:
		if self._t is None:
			return
		dt = now - self._t
		if dt > 0:
			for s, c in enumerate(self._counts):
				if c:
					self._seconds[s] += c * dt
			self._t = now

	def counts(self) -> dict[AgentState, int]:
		"""Agents in each state now."""
		with self._lock:
			return {AgentState(s): c for s, c in enumerate(self._counts)}

	def agent_seconds(self) -> dict[AgentState, float]:
		"""Agent-seconds spent in each state so far."""
		with self._lock:
			self._advance(time.monotonic())
			return {AgentState(s): v for s, v in enumerate(self._seconds)}

	def by_category(self) -> dict[str, float]:
		"""Agent-seconds working, waiting and making a model request."""
		out = {"working": 0.0, "waiting": 0.0, "model_request": 0.0}
		for state, seconds in self.agent_seconds().items():
			category = CATEGORY.get(state)
			if category is not None:
				out[category] += seconds
		return out

	def summary(self) -> dict:
		"""Agent-seconds by state and by category, and each category's share."""
		seconds = self.agent_seconds()
		categories = self.by_category()
		active = sum(categories.values())
		return {
			"agent_seconds": {s.name.lower(): round(v, 3) for s, v in seconds.items()},
			"agent_seconds_by_category": {
				k: round(v, 3) for k, v in categories.items()
			},
			"share_by_category": {
				k: (round(v / active, 4) if active else None)
				for k, v in categories.items()
			},
		}


class _Agent:
	"""The context variable's value: one live agent, with its open activities."""

	__slots__ = ("id", "states", "_open", "registered", "finished")

	def __init__(self, agent_id: str, states: AgentStates) -> None:
		self.id = agent_id
		self.states = states
		self.finished = False
		self._open = [0] * _N
		self.registered: dict[str, asyncio.Future] = {}

	def open(self, state: AgentState) -> None:
		self._open[state] += 1
		self._update()

	def close(self, state: AgentState) -> None:
		self._open[state] -= 1
		self._update()

	def _update(self) -> None:
		if self.finished:
			return
		for state in _PRECEDENCE:
			if self._open[state]:
				self.states.enter(self.id, state)
				return
		self.states.enter(self.id, AgentState.WORKING)


_current: ContextVar[_Agent | None] = ContextVar("flowgentic_agent", default=None)


def set_current_agent(agent_id: str, states: AgentStates) -> None:
	"""Make ``agent_id`` the current task's agent. The host calls it in each
	agent's own task, so it reaches that agent's nodes and tools only."""
	_current.set(_Agent(agent_id, states))


def current_agent() -> str | None:
	"""The id of the agent the current coroutine works for, if any."""
	agent = _current.get()
	return None if agent is None else agent.id


@contextmanager
def activity(state: AgentState) -> Iterator[None]:
	"""Count the current agent as in ``state`` for the duration of the block."""
	agent = _current.get()
	if agent is None:
		yield
		return
	agent.open(state)
	try:
		yield
	finally:
		agent.close(state)


# --- tasks agents wait on --------------------------------------------------------


def task_context() -> Context:
	"""A context for a task an agent asks for: the agent and the trace context,
	and nothing else of the caller's.

	An asyncio task keeps the context it was created in for as long as the task
	is kept, after it ends too. A tool runs inside its framework's run, whose
	context variables reach the agent's whole state (LangChain's run
	configuration reaches LangGraph's channels), so a task kept for repeat
	safety would keep that state alive for the rest of a campaign."""
	agent = _current.get()
	trace_context = otel_context.get_current()
	ctx = Context()
	ctx.run(_enter_task_context, agent, trace_context)
	return ctx


def _enter_task_context(
	agent: _Agent | None, trace_context: otel_context.Context
) -> None:
	_current.set(agent)
	otel_context.attach(trace_context)


_pending: dict[str, asyncio.Future] = {}


def register_pending(key: str, future: asyncio.Future) -> None:
	"""Record the future of a submitted task under its key."""
	_pending[key] = future
	agent = _current.get()
	if agent is not None:
		agent.registered[key] = future


def release_pending() -> None:
	"""Release this agent's task registrations when the runner finishes."""
	agent = _current.get()
	if agent is not None:
		agent.finished = True
		for key, future in agent.registered.items():
			if _pending.get(key) is future:
				_pending.pop(key)
		agent.registered.clear()


def forget_pending(key: str) -> None:
	_pending.pop(key, None)
	agent = _current.get()
	if agent is not None:
		agent.registered.pop(key, None)


def pending(key: str) -> asyncio.Future | None:
	"""The future of the task submitted under ``key`` in this process."""
	return _pending.get(key)


def registered(key: str) -> asyncio.Future | None:
	"""The future the current agent registered under ``key``, until it ends."""
	agent = _current.get()
	return None if agent is None else agent.registered.get(key)


# How many waits each future has, and how to withdraw a future whose maker can
# still take it back, such as a task its order has not admitted yet.
_waits: dict[asyncio.Future, int] = {}
_withdraw: dict[asyncio.Future, Callable[[], None]] = {}


def withdrawable(future: asyncio.Future, withdraw: Callable[[], None] | None) -> None:
	"""Call ``withdraw()`` if every wait on ``future`` is cancelled before it
	ends; ``None`` says it can no longer be withdrawn."""
	if withdraw is None:
		_withdraw.pop(future, None)
	else:
		_withdraw[future] = withdraw


async def wait(futures: Iterable[asyncio.Future]) -> None:
	"""Wait for ``futures`` to end, without raising their exceptions.

	Several waits may share a future, as calls made again with one key share
	their task. A future whose last wait is cancelled before it ends is
	withdrawn, if it still can be (``withdrawable``); otherwise it runs on."""
	futures = [f for f in futures if not f.done()]
	if not futures:
		return
	for future in futures:
		_waits[future] = _waits.get(future, 0) + 1
	try:
		await asyncio.wait(futures)
	finally:
		for future in futures:
			left = _waits[future] - 1
			if left:
				_waits[future] = left
				continue
			del _waits[future]
			if not future.done():
				withdraw = _withdraw.pop(future, None)
				if withdraw is not None:
					withdraw()
