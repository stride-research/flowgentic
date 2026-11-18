"""Running LangGraph graphs in an agent host.

``GraphRunner(graph)`` is the agent host's runner for a compiled LangGraph
graph: each agent is one thread, its id the thread id. A run that stops on
interrupts raised by Flowgentic's task tools is a suspension, which the host
resolves by awaiting the tasks and resuming the graph with ``Command(resume=...)``.
Any other interrupt ends the agent as interrupted.

``route_tool_calls`` sends each tool call of the model's last message to the
tool node as its own task (LangGraph's ``Send``). LangGraph saves the result of
each finished call with the checkpoint, so when an agent resumes from a
suspension only the call that suspended runs again, and no other tool does.

Each resume is a new LangGraph run, and LangGraph counts a run's steps against
``recursion_limit`` from where that run starts. On its own, the limit would
never stop an agent that suspends on every turn. So the runner also lets an
agent resume at most ``recursion_limit`` times: each resume runs at least one
step, so an agent that would finish within the limit without suspending is
never stopped by it, and one that loops on a suspending tool is. An agent's
count lasts until it ends, or until the asyncio task that resumed it ends, as
when the host cancels or fails an agent while it waits: the agent host runs
each agent's start and resumes in one task.

A graph compiled with ``interrupt_before`` or ``interrupt_after`` is refused:
a static breakpoint stops a run without an interrupt in its output, so the
agent would end as finished, with partial output.

A run that stops at a task tool's interrupt is freed when it stops. The
interrupt is an exception, and the frames it passed through in LangChain and
LangGraph keep it, or its traceback, in their locals: reference cycles that
only the cyclic collector frees. With thousands of agents in a process, and the
collector's first threshold raised as the host does, those cycles reach the
older generations and make each collection longer. So the task tool records
each interrupt it raises, and the runner clears the frames of its traceback
once the run has stopped. When several tool calls of one step suspend,
LangGraph's runner combines their interrupts into an exception of its own, and
that one's frames are left to the collector.
"""

from __future__ import annotations

import asyncio
import traceback
from contextvars import ContextVar
from functools import partial
from typing import Any, Literal

from langgraph.errors import GraphRecursionError
from langgraph.graph import END
from langgraph.types import Command, Send

from flowgentic.host import Agent, Finished, Interrupted, Step, Suspended

try:  # LangGraph's limit for a run whose config sets none
	from langgraph._internal._config import DEFAULT_RECURSION_LIMIT as _DEFAULT_LIMIT
except ImportError:  # pragma: no cover - a LangGraph that moved it
	_DEFAULT_LIMIT = 10_000

__all__ = ["GraphRunner", "PENDING_KEY", "record_interrupt", "route_tool_calls"]

PENDING_KEY = "flowgentic_pending"
"""The key of the interrupt value a task tool raises: the task's key."""

Durability = Literal["sync", "async", "exit"]

# The interrupts task tools raised in the current run, to free once it stops.
# Unset outside a GraphRunner, so a tool run another way keeps nothing.
_raised: ContextVar[list[BaseException] | None] = ContextVar(
	"flowgentic_raised", default=None
)


def record_interrupt(exc: BaseException) -> None:
	"""Remember an interrupt raised in the current run, for the runner to free
	when the run stops."""
	raised = _raised.get()
	if raised is not None:
		raised.append(exc)


def _free(raised: list[BaseException]) -> None:
	# The run has stopped: every frame these exceptions passed through has
	# returned, so clearing their locals changes nothing but what they keep.
	for exc in raised:
		traceback.clear_frames(exc.__traceback__)
		exc.__traceback__ = None
	raised.clear()


class GraphRunner:
	"""Runs one compiled graph for many agents; see the module's docstring.

	``config`` is merged under each agent's own; ``durability`` is LangGraph's.
	``exit``, the default, saves a checkpoint only when a run ends or suspends.
	An agent resumes at most as many times as its run's ``recursion_limit``;
	one more resume fails it with ``GraphRecursionError``.
	"""

	def __init__(
		self,
		graph: Any,
		*,
		config: dict | None = None,
		durability: Durability = "exit",
	) -> None:
		if getattr(graph, "interrupt_before_nodes", None) or getattr(
			graph, "interrupt_after_nodes", None
		):
			raise ValueError(
				"GraphRunner cannot run a graph compiled with interrupt_before or "
				"interrupt_after: a static breakpoint stops a run without an "
				"interrupt, so the agent would end as finished, with partial "
				"output. Call interrupt() in a node or a tool instead."
			)
		self.graph = graph
		self.config = dict(config or {})
		self.durability = durability
		# Resumes per suspended agent, each in a cell of its own, so that a
		# count dropped and made again for the same id is not dropped twice.
		self._resumes: dict[str, list[int]] = {}

	def _config(self, agent: Agent) -> dict:
		config = {**self.config, **(agent.config or {})}
		configurable = {
			**self.config.get("configurable", {}),
			**((agent.config or {}).get("configurable", {})),
			"thread_id": agent.id,
		}
		config["configurable"] = configurable
		return config

	def _limit(self, config: dict) -> int:
		limit = config.get("recursion_limit")
		if limit is None:  # the graph's own, from with_config, or LangGraph's
			limit = (getattr(self.graph, "config", None) or {}).get("recursion_limit")
		return _DEFAULT_LIMIT if limit is None else limit

	async def start(self, agent: Agent) -> Step:
		self._resumes.pop(agent.id, None)
		return await self._run(agent.input, agent, self._config(agent))

	async def resume(self, agent: Agent, done: dict[str, Any]) -> Step:
		config = self._config(agent)
		limit = self._limit(config)
		count = self._count(agent.id)
		count[0] += 1
		if count[0] > limit:
			self._resumes.pop(agent.id, None)
			raise GraphRecursionError(
				f"Agent {agent.id!r} suspended more than {limit} times, its "
				"recursion_limit. Each resume runs at least one step; raise "
				"recursion_limit in the run's config if the agent should go on."
			)
		return await self._run(Command(resume=done), agent, config)

	def _count(self, agent_id: str) -> list[int]:
		count = self._resumes.get(agent_id)
		if count is None:
			count = self._resumes[agent_id] = [0]
			# An agent cancelled or failed by the host while it waits never
			# comes back here: its count goes when the task running it ends.
			task = asyncio.current_task()
			if task is not None:
				task.add_done_callback(partial(self._forget, agent_id, count))
		return count

	def _forget(self, agent_id: str, count: list[int], _task: asyncio.Task) -> None:
		if self._resumes.get(agent_id) is count:
			del self._resumes[agent_id]

	async def _run(self, value: Any, agent: Agent, config: dict) -> Step:
		raised: list[BaseException] = []
		token = _raised.set(raised)
		try:
			out = await self.graph.ainvoke(value, config, durability=self.durability)
		except BaseException:
			self._resumes.pop(agent.id, None)
			raise
		finally:
			_raised.reset(token)
			_free(raised)
		step = _step(out)
		if not isinstance(step, Suspended):
			self._resumes.pop(agent.id, None)
		return step


def _step(out: Any) -> Step:
	interrupts = out.get("__interrupt__") if isinstance(out, dict) else None
	if not interrupts:
		return Finished(out)
	waits = []
	for interrupt in interrupts:
		value = interrupt.value
		if not (isinstance(value, dict) and PENDING_KEY in value):
			return Interrupted(tuple(interrupts))
		waits.append((interrupt.id, value[PENDING_KEY]))
	return Suspended(tuple(waits))


def route_tool_calls(state: Any) -> Any:
	"""A conditional edge after the model node: sends each tool call of the
	last message in the state's ``messages`` to the node named ``tools``, each
	as its own task, or ends the graph when there is none."""
	messages = state["messages"] if isinstance(state, dict) else state.messages
	calls = getattr(messages[-1], "tool_calls", None) if messages else None
	if not calls:
		return END
	return [Send("tools", [call]) for call in calls]
