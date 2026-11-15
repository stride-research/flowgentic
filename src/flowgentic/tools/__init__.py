"""Where an agent's tools run.

A tool declares where it runs by how it is made:

- **Inline:** any tool of the agent's framework, such as a LangChain ``@tool``.
  It runs in the agent's process, on its event loop. For short Python.
- **Task:** ``task_tool(fn)``. ``fn`` runs as a RHAPSODY function task in the
  agent process's session: compute the agent waits for, such as a simulation.
- **Service:** ``mcp_tool(name, ...)``. The call goes to the shared tool
  service, which holds a supervised pool of MCP clients.

The declarations are framework-neutral; each framework's adapter turns them
into its own tools (``flowgentic.langgraph.bind_tools`` for LangGraph).

A ``ToolRuntime`` runs them for one agent process. A task or service call is
keyed by the agent and the tool call's id, so a call made again with the same
key, as a framework does when it resumes an agent, gets the first call's
result and runs nothing. The runtime keeps the outcomes of the last
``retained`` finished tasks.

A task may also **suspend** its agent: the agent's state is saved and dropped
while the task runs, and the host resumes it when the task ends.

When tasks reach RHAPSODY is a policy: ``TaskOrder``. ``Immediate()`` submits
each task at once. Another policy can hold tasks and release them in an order
of its choosing, such as a campaign's priorities.
"""

from __future__ import annotations

import asyncio
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
	"TaskTool",
	"ToolRuntime",
	"mcp_tool",
	"task_tool",
]

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


# --- declarations ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskTool:
	"""A tool that runs ``fn`` as a RHAPSODY function task.

	``suspend``: whether the agent suspends while the task runs: a bool, or a
	function of the agent's id and the call's arguments. It is asked again when
	the agent resumes, so it must give the same answer for the same call.
	``task``: fields added to the RHAPSODY task's description.
	"""

	fn: Callable[..., Any]
	name: str
	description: str
	suspend: bool | Callable[[str, dict[str, Any]], bool] = False
	task: dict[str, Any] = field(default_factory=dict)

	def suspends(self, agent_id: str, args: dict[str, Any]) -> bool:
		if callable(self.suspend):
			return bool(self.suspend(agent_id, args))
		return self.suspend


def task_tool(
	fn: Callable[..., Any] | None = None,
	*,
	suspend: bool | Callable[[str, dict[str, Any]], bool] = False,
	name: str | None = None,
	description: str | None = None,
	**task: Any,
) -> Any:
	"""Declare ``fn`` a task tool; usable as ``@task_tool`` or
	``@task_tool(suspend=True)``. ``fn`` must be importable by the workers: a
	top-level function."""

	def make(f: Callable[..., Any]) -> TaskTool:
		return TaskTool(
			fn=f,
			name=name or f.__name__,
			description=description or (f.__doc__ or "").strip() or f.__name__,
			suspend=suspend,
			task=dict(task),
		)

	return make(fn) if fn is not None else make


@dataclass(frozen=True, slots=True)
class McpTool:
	"""A tool on an MCP server, called through the tool service.

	``args_schema``: a pydantic model of the tool's arguments.
	"""

	name: str
	description: str
	args_schema: Any


def mcp_tool(name: str, *, description: str, args_schema: Any) -> McpTool:
	"""Declare an MCP tool by name."""
	return McpTool(name=name, description=description, args_schema=args_schema)


# --- order of tasks ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskInfo:
	"""What an order policy knows about a task waiting to be submitted."""

	key: str
	agent_id: str
	tool: str
	args: dict[str, Any]


class TaskOrder(Protocol):
	"""When tasks reach RHAPSODY."""

	async def admit(self, info: TaskInfo) -> None:
		"""Return when the task may be submitted."""
		...

	def done(self, info: TaskInfo) -> None:
		"""The task has ended."""
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

	``session``: a RHAPSODY ``Session``, for task tools. ``service``: a
	``flowgentic.tools.service.ToolServiceClient``, for MCP tools.
	"""

	def __init__(
		self,
		session: Any = None,
		service: Any = None,
		*,
		order: TaskOrder | None = None,
		retained: int = 100_000,
	) -> None:
		if retained < 0:
			raise ValueError("retained must be 0 or more")
		self.session = session
		self.service = service
		self.order = order or Immediate()
		self.retained = retained
		self._jobs: dict[str, asyncio.Future] = {}
		self._ended: OrderedDict[str, None] = OrderedDict()
		self.submitted = 0
		self.repeats = 0
		self.failed = 0

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
		submitted under the same key."""
		key = self.key(agent_id, call_id)
		job = self._jobs.get(key)
		if job is not None:
			self.repeats += 1
			return job
		if self.session is None:
			raise RuntimeError("task tools need a ToolRuntime with a RHAPSODY session")
		info = TaskInfo(key=key, agent_id=agent_id, tool=tool.name, args=dict(args))
		job = asyncio.ensure_future(self._run(tool, info))
		self._jobs[key] = job
		_agents.register_pending(key, job)
		self.submitted += 1
		return job

	async def _run(self, tool: TaskTool, info: TaskInfo) -> Any:
		t = time.monotonic()
		outcome = "ok"
		await self.order.admit(info)
		try:
			return await _rhapsody.run_function(
				self.session, tool.fn, info.args, uid=self.uid(info.key), **tool.task
			)
		except Exception:
			self.failed += 1
			outcome = "error"
			raise
		finally:
			self.order.done(info)
			_telemetry.TOOL_DURATION.record(
				time.monotonic() - t,
				{
					_telemetry.GEN_AI_TOOL_NAME: tool.name,
					_telemetry.TOOL_KIND: "task",
					_telemetry.TOOL_OUTCOME: outcome,
				},
			)
			self._ended_add(info.key)

	def _ended_add(self, key: str) -> None:
		self._ended[key] = None
		while len(self._ended) > self.retained:
			old, _ = self._ended.popitem(last=False)
			self._jobs.pop(old, None)
			_agents.forget_pending(old)

	async def call_service(
		self, tool: McpTool, agent_id: str, call_id: str, args: dict[str, Any]
	) -> Any:
		"""Call an MCP tool through the tool service; a repeated key gets the
		first call's answer."""
		if self.service is None:
			raise RuntimeError(
				"MCP tools need a ToolRuntime with a tool service client"
			)
		t = time.monotonic()
		outcome = "ok"
		try:
			return await self.service.call(self.key(agent_id, call_id), tool.name, args)
		except Exception:
			outcome = "error"
			raise
		finally:
			_telemetry.TOOL_DURATION.record(
				time.monotonic() - t,
				{
					_telemetry.GEN_AI_TOOL_NAME: tool.name,
					_telemetry.TOOL_KIND: "service",
					_telemetry.TOOL_OUTCOME: outcome,
				},
			)

	def stats(self) -> dict:
		return {
			"submitted": self.submitted,
			"repeats": self.repeats,
			"failed": self.failed,
			"retained": len(self._ended),
		}
