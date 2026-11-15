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

import traceback
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, Literal

from langgraph.graph import END
from langgraph.types import Command, Send

from flowgentic.host import Agent, Finished, Interrupted, Step, Suspended

__all__ = ["GraphRunner", "PENDING_KEY", "route_tool_calls", "tool_call_router"]

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
	"""

	def __init__(
		self,
		graph: Any,
		*,
		config: dict | None = None,
		durability: Durability = "exit",
	) -> None:
		self.graph = graph
		self.config = dict(config or {})
		self.durability = durability

	def _config(self, agent: Agent) -> dict:
		config = {**self.config, **(agent.config or {})}
		configurable = {
			**self.config.get("configurable", {}),
			**((agent.config or {}).get("configurable", {})),
			"thread_id": agent.id,
		}
		config["configurable"] = configurable
		return config

	async def start(self, agent: Agent) -> Step:
		return await self._run(agent.input, agent)

	async def resume(self, agent: Agent, done: dict[str, Any]) -> Step:
		return await self._run(Command(resume=done), agent)

	async def _run(self, value: Any, agent: Agent) -> Step:
		raised: list[BaseException] = []
		token = _raised.set(raised)
		try:
			out = await self.graph.ainvoke(
				value, self._config(agent), durability=self.durability
			)
		finally:
			_raised.reset(token)
			_free(raised)
		return _step(out)


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


def tool_call_router(
	tools_node: str = "tools", messages_key: str = "messages"
) -> Callable[[Any], Any]:
	"""A conditional edge that sends each tool call of the last message to
	``tools_node`` as its own task, or ends the graph when there is none."""

	def route(state: Any) -> Any:
		messages = (
			state[messages_key]
			if isinstance(state, dict)
			else getattr(state, messages_key)
		)
		calls = getattr(messages[-1], "tool_calls", None) if messages else None
		if not calls:
			return END
		return [Send(tools_node, [call]) for call in calls]

	return route


route_tool_calls = tool_call_router()
"""``tool_call_router()`` for a node named ``tools`` and a ``messages`` key."""
