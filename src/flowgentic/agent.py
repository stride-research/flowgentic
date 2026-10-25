"""Framework-neutral agent components executed through AsyncFlow.

This module provides the inverse of an AsyncFlow-backed agent tool: it makes an
agent-framework component callable as an AsyncFlow task.  Applications can keep
their orchestration and scientific state in ordinary Python and AsyncFlow while
using LangGraph, CrewAI, Academy, or another framework only inside agents.

Each call of a bound agent is an OpenTelemetry ``invoke_agent`` span in the
caller's process, with the AsyncFlow task that ran it as a child.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from opentelemetry.trace import SpanKind

from flowgentic import _asyncflow, _telemetry

if TYPE_CHECKING:
	from radical.asyncflow import WorkflowEngine


class ModelDumpable(Protocol):
	"""Structured object exposing the Pydantic-compatible dump operation."""

	def model_dump(self) -> dict[str, Any]:
		"""Return the structured decision as a plain mapping."""
		...


AgentResponse = Mapping[str, Any] | ModelDumpable


class AgentComponent(Protocol):
	"""Minimal interface implemented by an agent-framework component."""

	async def ainvoke(self, context: Mapping[str, Any]) -> AgentResponse:
		"""Return a structured decision for the supplied bounded context."""
		...


AgentSubmitter = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class AgentTask:
	"""An addressable agent component placed on an AsyncFlow backend."""

	identity: str
	framework: str
	backend: str
	_submit: AgentSubmitter

	async def ainvoke(self, context: Mapping[str, Any]) -> dict[str, Any]:
		"""Invoke the agent through AsyncFlow and return decision plus metadata."""
		with _telemetry.tracer.start_as_current_span(
			f"{_telemetry.INVOKE_AGENT} {self.identity}",
			kind=SpanKind.INTERNAL,
			attributes={
				_telemetry.GEN_AI_OPERATION_NAME: _telemetry.INVOKE_AGENT,
				_telemetry.GEN_AI_AGENT_NAME: self.identity,
				_telemetry.TASK_BACKEND: self.backend,
			},
		) as span:
			future = self._submit(dict(context))
			try:
				return await future
			except Exception as exc:
				span.set_attribute(_telemetry.ERROR_TYPE, type(exc).__qualname__)
				raise
			finally:
				task_uid = _asyncflow.uid(future)
				if task_uid is not None:
					span.set_attribute(_telemetry.TASK_UID, task_uid)


def bind_agent(
	flow: WorkflowEngine,
	component: AgentComponent,
	*,
	identity: str,
	framework: str,
	backend: str = "ai",
) -> AgentTask:
	"""Bind one framework-specific agent component to an AsyncFlow task.

	The surrounding application sees a stable, framework-neutral ``AgentTask``.
	The component remains free to use a LangGraph graph, CrewAI crew, Academy
	actor, direct LLM call, or scripted rehearsal implementation internally.
	"""

	async def invoke_agent(context: dict[str, Any]) -> dict[str, Any]:
		started = time.perf_counter()
		response = await component.ainvoke(context)
		finished = time.perf_counter()

		return {
			"agent": identity,
			"framework": framework,
			"decision": _structured_payload(response),
			"event": {
				"name": f"agent.{identity}",
				"backend": backend,
				"cycle": int(context.get("cycle", -1)),
				"started": started,
				"finished": finished,
				"duration": finished - started,
			},
		}

	invoke_agent.__name__ = f"agent_{identity.replace('-', '_')}"
	submit = _asyncflow.task(flow, invoke_agent, backend=backend)
	return AgentTask(identity, framework, backend, submit)


def _structured_payload(response: AgentResponse) -> dict[str, Any]:
	"""Normalize Pydantic and mapping outputs at the framework boundary."""
	if hasattr(response, "model_dump"):
		payload = response.model_dump()
	elif isinstance(response, Mapping):
		payload = dict(response)
	else:
		raise TypeError(
			"Agent components must return a mapping or Pydantic model, "
			f"not {type(response).__name__}."
		)

	return payload
