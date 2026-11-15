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
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from langgraph.graph import END
from langgraph.types import Command, Send

from flowgentic.host import Agent, Finished, Interrupted, Step, Suspended

__all__ = ["GraphRunner", "PENDING_KEY", "route_tool_calls", "tool_call_router"]

PENDING_KEY = "flowgentic_pending"
"""The key of the interrupt value a task tool raises: the task's key."""

Durability = Literal["sync", "async", "exit"]


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
		out = await self.graph.ainvoke(
			agent.input, self._config(agent), durability=self.durability
		)
		return _step(out)

	async def resume(self, agent: Agent, done: dict[str, Any]) -> Step:
		out = await self.graph.ainvoke(
			Command(resume=done), self._config(agent), durability=self.durability
		)
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
