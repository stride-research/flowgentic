"""Running LangGraph graphs in an agent host.

``GraphRunner(graph)`` is the agent host's runner for a compiled LangGraph
graph: each agent is one thread, its id the thread id. A run that stops on
interrupts raised by Flowgentic's task tools is a suspension, which the host
resolves by awaiting the tasks and resuming the graph with ``Command(resume=...)``.
Any other interrupt ends the agent as interrupted.

A finished agent's thread stays in the graph's checkpointer, so the store grows
with the campaign. With ``delete_finished=True`` the runner deletes it when the
agent finishes, so that the store holds the agents still running: what a store
of fixed size, such as a Dragon dictionary, needs. An interrupted agent's
thread is kept even then, for a human-in-the-loop interrupt resumes from it,
and so is a failed or cancelled agent's, to debug or retry it.

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
count lasts until its run ends, or until the agent host ends the agent, as when
it cancels or fails an agent that waits between two resumes.

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

import logging
import traceback
from contextvars import ContextVar
from functools import partial
from typing import Any, Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END
from langgraph.types import Command, Send

from flowgentic import _agents
from flowgentic.host import Agent, Finished, Interrupted, Step, Suspended

try:  # LangGraph's limit for a run whose config sets none
	from langgraph._internal._config import DEFAULT_RECURSION_LIMIT as _DEFAULT_LIMIT
except ImportError:  # pragma: no cover - a LangGraph that moved it
	_DEFAULT_LIMIT = 10_000

__all__ = ["GraphRunner", "PENDING_KEY", "record_interrupt", "route_tool_calls"]

log = logging.getLogger(__name__)

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

	With ``delete_finished=True``, a finished agent's thread is deleted from the
	graph's checkpointer once its run ends, before the host hands its output to
	``on_done``. Two things are lost with it: ``on_done`` can no longer read the
	thread's state (``graph.get_state`` finds it empty), and an agent id run
	again, as a conversation continued, starts a fresh thread. A thread that
	cannot be deleted is logged, and the agent still finishes;
	``delete_failures`` counts them.
	"""

	def __init__(
		self,
		graph: Any,
		*,
		config: dict | None = None,
		durability: Durability = "exit",
		delete_finished: bool = False,
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
		self.delete_finished = delete_finished
		self.delete_failures = 0
		self._cannot_delete = False
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
			# An agent the host cancels or fails while it waits never comes
			# back here: its count goes when the host ends it.
			_agents.at_agent_end(agent_id, partial(self._forget, agent_id, count))
		return count

	def _forget(self, agent_id: str, count: list[int]) -> None:
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
		if isinstance(step, Finished) and self.delete_finished:
			await self._delete(agent.id)
		return step

	async def _delete(self, thread_id: str) -> None:
		saver = getattr(self.graph, "checkpointer", None)
		if not isinstance(saver, BaseCheckpointSaver) or self._cannot_delete:
			return
		try:
			await saver.adelete_thread(thread_id)
		except NotImplementedError:
			# A saver that cannot delete never will: say so once, and stop asking.
			self._cannot_delete = True
			log.warning(
				"%s cannot delete threads: finished agents' checkpoints stay in "
				"it. Leave delete_finished off to keep them without this warning.",
				type(saver).__name__,
			)
		except Exception as exc:
			# The agent finished, and its output stands; only its thread stays.
			self.delete_failures += 1
			log.log(
				logging.WARNING if self.delete_failures == 1 else logging.DEBUG,
				"could not delete finished agent %s's thread (%s: %s); its "
				"checkpoint stays in the store",
				thread_id,
				type(exc).__name__,
				exc,
				exc_info=self.delete_failures == 1,
			)


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
