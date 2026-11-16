"""Where an agent's tools run.

A tool declares where it runs by how it is made:

- **Inline:** any tool of the agent's framework, such as a LangChain ``@tool``.
  It runs in the agent's process, on its event loop. For short Python.
- **Task:** ``task_tool(fn)``. ``fn`` runs as a RHAPSODY function task in the
  agent process's session: compute the agent waits for, such as a simulation.
- **Service:** ``mcp_tool(name, ...)``. The call goes to the tool service
  (``flowgentic.tools.service``), one process that calls MCP tools for many
  agent processes through a supervised pool of clients
  (``flowgentic.tools.mcp``).

The declarations are framework-neutral; each framework's adapter turns them
into its own tools (``flowgentic.langgraph.bind_tools`` for LangGraph).

A ``ToolRuntime`` runs them for one agent process. A task or service call is
keyed by the agent and the tool call's id, so a call made again with the same
key, as a framework does when it resumes an agent, gets the first call's
result and runs nothing. The runtime keeps the outcomes of the last
``retained`` finished tasks; the tool service keeps its own answers.

A task may also **suspend** its agent: the agent's state is saved and dropped
while the task runs, and the agent host resumes it when the task ends.

When tasks reach RHAPSODY is a policy: ``TaskOrder``. ``Immediate()`` submits
each task at once. Another policy can hold tasks and release them in an order
of its choosing, such as a campaign's priorities, or refuse them, as a budget
does.

Task tools need the ``rhapsody`` extra when they run; declaring them, MCP
tools and the agent processes' side of the tool service need no extra.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from flowgentic import _agents, _rhapsody, _telemetry

__all__ = [
	"Immediate",
	"McpTool",
	"TaskInfo",
	"TaskOrder",
	"TaskRecord",
	"TaskTool",
	"ToolRuntime",
	"mcp_tool",
	"task_tool",
]

log = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


# --- declarations ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskTool:
	"""A tool that runs ``fn`` as a RHAPSODY function task.

	``suspend``: whether the agent suspends while the task runs: a bool, or a
	function of the agent's id and the call's arguments. It is asked again when
	the agent resumes, so it must give the same answer for the same call.
	``task``: fields added to the RHAPSODY task's description.

	What the model sees can differ from what the worker runs:
	``args_schema`` (a pydantic model) gives the arguments the model sees, by
	default ``fn``'s parameters; ``prepare(agent_id, call_id, args)`` returns the
	keyword arguments ``fn`` is called with, adding what the model must not see,
	such as where to write a result; ``output(value)`` turns ``fn``'s result
	into what the model reads.
	"""

	fn: Callable[..., Any]
	name: str
	description: str
	suspend: bool | Callable[[str, dict[str, Any]], bool] = False
	task: dict[str, Any] = field(default_factory=dict)
	args_schema: Any = None
	prepare: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None
	output: Callable[[Any], Any] | None = None

	def suspends(self, agent_id: str, args: dict[str, Any]) -> bool:
		"""Whether this call suspends its agent."""
		if callable(self.suspend):
			return bool(self.suspend(agent_id, args))
		return self.suspend


def task_tool(
	fn: Callable[..., Any] | None = None,
	*,
	suspend: bool | Callable[[str, dict[str, Any]], bool] = False,
	name: str | None = None,
	description: str | None = None,
	args_schema: Any = None,
	prepare: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
	output: Callable[[Any], Any] | None = None,
	**task: Any,
) -> Any:
	"""Declare ``fn`` a task tool; usable as ``@task_tool`` or
	``@task_tool(suspend=True)``. ``fn`` must be importable by the workers: a
	top-level function. See ``TaskTool`` for the options."""

	def make(f: Callable[..., Any]) -> TaskTool:
		return TaskTool(
			fn=f,
			name=name or f.__name__,
			description=description or (f.__doc__ or "").strip() or f.__name__,
			suspend=suspend,
			task=dict(task),
			args_schema=args_schema,
			prepare=prepare,
			output=output,
		)

	return make(fn) if fn is not None else make


@dataclass(frozen=True, slots=True)
class McpTool:
	"""A tool on an MCP server, called through the tool service.

	``args_schema``: a pydantic model of the tool's arguments. ``idempotent``:
	whether the tool can safely run twice, as a lookup can; only then does the
	service send a call again after its connection was lost.
	"""

	name: str
	description: str
	args_schema: Any
	idempotent: bool = False


def mcp_tool(
	name: str, *, description: str, args_schema: Any, idempotent: bool = False
) -> McpTool:
	"""Declare an MCP tool by name. See ``McpTool`` for the options."""
	return McpTool(
		name=name,
		description=description,
		args_schema=args_schema,
		idempotent=idempotent,
	)


# --- order of tasks ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskInfo:
	"""What an order policy knows about a task waiting to be submitted: the
	model's arguments, and the keyword arguments ``fn`` will be called with."""

	key: str
	agent_id: str
	call_id: str
	tool: str
	args: dict[str, Any]
	kwargs: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TaskRecord:
	"""One task, for listeners: when it was asked for, admitted and ended
	(``time.time()``), how it ended, and its value or error.

	``outcome`` is ``ok``, ``error`` (the task failed), ``refused`` (the order
	policy refused it; ``t_admit`` is ``None``) or ``cancelled``.
	"""

	info: TaskInfo
	t_submit: float
	t_admit: float | None
	t_end: float
	outcome: str
	value: Any = None
	error: str | None = None

	@property
	def ok(self) -> bool:
		"""Whether the task returned a value."""
		return self.outcome == "ok"


class TaskOrder(Protocol):
	"""When, and whether, tasks reach RHAPSODY."""

	async def admit(self, info: TaskInfo) -> None:
		"""Return when the task may be submitted, or raise to refuse it: the
		agent reads the error, as it reads a failed task's."""
		...

	def done(self, info: TaskInfo) -> None:
		"""An admitted task has ended. Not called for a refused task."""
		...


class Immediate:
	"""Submit every task at once, in the order agents ask."""

	async def admit(self, info: TaskInfo) -> None:
		return None

	def done(self, info: TaskInfo) -> None:
		return None


# --- the runtime -------------------------------------------------------------------


class ToolRuntime:
	"""Runs task and service tools for one agent process.

	``session``: a RHAPSODY ``Session``, for task tools. ``service``: a started
	``flowgentic.tools.service.ToolServiceClient``, for MCP tools. ``order``:
	when tasks reach RHAPSODY, ``Immediate()`` by default. ``retained``: how
	many ended tasks are kept, for repeat safety.

	A task asked for by an agent the host runs is registered for the host
	(``flowgentic.host.register_pending``) until that agent ends, so the host
	can resume the agent when the task ends, and the agent finds the task when
	it resumes, even once more than ``retained`` tasks have ended since.
	"""

	def __init__(
		self,
		session: Any = None,
		service: Any = None,
		*,
		order: TaskOrder | None = None,
		retained: int = 100_000,
	) -> None:
		if retained < 1:
			raise ValueError("retained must be at least 1")
		self.session = session
		self.service = service
		self.order = order or Immediate()
		self.retained = retained
		self._jobs: dict[str, asyncio.Future] = {}
		self._ended: OrderedDict[str, None] = OrderedDict()
		self.submitted = 0
		self.repeats = 0
		self.failed = 0
		self.refused = 0
		self._listeners: list[Callable[[TaskRecord], None]] = []

	def add_listener(self, listener: Callable[[TaskRecord], None]) -> None:
		"""Call ``listener(record)`` as each task ends, before its agent is
		told. A listener's error is logged and does not change the task's."""
		self._listeners.append(listener)

	@staticmethod
	def key(agent_id: str, call_id: str) -> str:
		"""The key of one tool call: the agent and the call's id."""
		return f"{agent_id}/{call_id}"

	@staticmethod
	def uid(key: str) -> str:
		"""A RHAPSODY task uid made from a key."""
		return "flowgentic." + _UNSAFE.sub("_", key.replace("/", "."))

	def submit(
		self, tool: TaskTool, agent_id: str, call_id: str, args: dict[str, Any]
	) -> asyncio.Future:
		"""The future of the task for this call: a new task, or the one already
		submitted under the same key.

		A failed task keeps its exception for as long as it is kept. Read it
		with ``exception()`` rather than raise it in a framework's code, whose
		frames its traceback would then keep."""
		key = self.key(agent_id, call_id)
		job = self._jobs.get(key)
		if job is None:
			# Dropped from the runtime's ended tasks, but still registered by the
			# agent asking: an agent resuming after other tasks ended finds it.
			job = _agents.registered(key)
		if job is not None:
			self.repeats += 1
			return job
		if self.session is None:
			raise RuntimeError("task tools need a ToolRuntime with a RHAPSODY session")
		kwargs = (
			dict(args)
			if tool.prepare is None
			else tool.prepare(agent_id, call_id, dict(args))
		)
		info = TaskInfo(
			key=key,
			agent_id=agent_id,
			call_id=call_id,
			tool=tool.name,
			args=dict(args),
			kwargs=kwargs,
		)
		# An asyncio task keeps the context it was created in, and the runtime
		# keeps ended tasks: in the caller's context, a kept task would keep the
		# framework's run, and with it the agent's whole state.
		job = asyncio.get_running_loop().create_task(
			self._run(tool, info), context=_agents.task_context()
		)
		self._jobs[key] = job
		if _agents.current_agent() is not None:
			_agents.register_pending(key, job)  # released when the agent ends
		self.submitted += 1
		return job

	async def _run(self, tool: TaskTool, info: TaskInfo) -> Any:
		t = time.monotonic()
		t_submit = time.time()
		t_admit: float | None = None
		value: Any = None
		error: str | None = None
		outcome = "error"
		with _telemetry.tracer.start_as_current_span(
			f"{_telemetry.EXECUTE_TOOL} {tool.name}",
			attributes={
				_telemetry.GEN_AI_OPERATION_NAME: _telemetry.EXECUTE_TOOL,
				_telemetry.GEN_AI_TOOL_NAME: tool.name,
				_telemetry.GEN_AI_TOOL_CALL_ID: info.call_id,
				_telemetry.TOOL_KIND: "task",
			},
		) as span:
			try:
				try:
					await self.order.admit(info)
				except Exception:
					outcome = "refused"
					self.refused += 1
					raise
				t_admit = time.time()
				try:
					value = await _rhapsody.run_function(
						self.session,
						tool.fn,
						info.kwargs,
						uid=self.uid(info.key),
						**tool.task,
					)
				finally:
					self.order.done(info)
				outcome = "ok"
				return value
			except asyncio.CancelledError:
				outcome = "cancelled"
				error = "CancelledError"
				raise
			except Exception as exc:
				if outcome == "error":
					self.failed += 1
				error = f"{type(exc).__name__}: {exc}"
				span.set_attribute(_telemetry.ERROR_TYPE, type(exc).__qualname__)
				raise
			finally:
				span.set_attribute(_telemetry.TOOL_OUTCOME, outcome)
				self._ended_add(info.key)
				self._tell(
					TaskRecord(
						info, t_submit, t_admit, time.time(), outcome, value, error
					)
				)
				_telemetry.TOOL_DURATION.record(
					time.monotonic() - t,
					{
						_telemetry.GEN_AI_TOOL_NAME: tool.name,
						_telemetry.TOOL_KIND: "task",
						_telemetry.TOOL_OUTCOME: outcome,
					},
				)

	def _tell(self, record: TaskRecord) -> None:
		for listener in self._listeners:
			try:
				listener(record)
			except Exception:
				log.warning(
					"a task listener failed on %s", record.info.key, exc_info=True
				)

	def _ended_add(self, key: str) -> None:
		self._ended[key] = None
		while len(self._ended) > self.retained:
			old, _ = self._ended.popitem(last=False)
			self._jobs.pop(old, None)

	async def call_service(
		self, tool: McpTool, agent_id: str, call_id: str, args: dict[str, Any]
	) -> Any:
		"""Call an MCP tool through the tool service; a repeated key gets the
		service's kept answer. ``RuntimeError`` means the runtime is set up
		wrong: no service client, or one not started."""
		if self.service is None:
			raise RuntimeError(
				"MCP tools need a ToolRuntime with a tool service client"
			)
		t = time.monotonic()
		outcome = "error"
		with _telemetry.tracer.start_as_current_span(
			f"{_telemetry.EXECUTE_TOOL} {tool.name}",
			attributes={
				_telemetry.GEN_AI_OPERATION_NAME: _telemetry.EXECUTE_TOOL,
				_telemetry.GEN_AI_TOOL_NAME: tool.name,
				_telemetry.GEN_AI_TOOL_CALL_ID: call_id,
				_telemetry.TOOL_KIND: "service",
			},
		) as span:
			try:
				value = await self.service.call(
					self.key(agent_id, call_id),
					tool.name,
					args,
					idempotent=tool.idempotent,
				)
				outcome = "ok"
				return value
			except asyncio.CancelledError:
				outcome = "cancelled"
				raise
			except Exception as exc:
				span.set_attribute(_telemetry.ERROR_TYPE, type(exc).__qualname__)
				raise
			finally:
				span.set_attribute(_telemetry.TOOL_OUTCOME, outcome)
				_telemetry.TOOL_DURATION.record(
					time.monotonic() - t,
					{
						_telemetry.GEN_AI_TOOL_NAME: tool.name,
						_telemetry.TOOL_KIND: "service",
						_telemetry.TOOL_OUTCOME: outcome,
					},
				)

	def stats(self) -> dict:
		"""Tasks submitted, asked for again, failed and refused, and how many
		ended tasks are kept."""
		return {
			"submitted": self.submitted,
			"repeats": self.repeats,
			"failed": self.failed,
			"refused": self.refused,
			"retained": len(self._ended),
		}
