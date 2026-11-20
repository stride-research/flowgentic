"""Where an agent's tools run.

A tool declares where it runs by how it is made:

- **Inline:** any tool of the agent's framework, such as a LangChain ``@tool``.
  It runs in the agent's process, on its event loop. For short Python.
- **Task:** ``task_tool(fn)``. ``fn`` runs as a function task: compute the
  agent waits for, such as a simulation. It runs in the agent process's
  RHAPSODY session, or, in an AsyncFlow application, as an AsyncFlow task on a
  named backend of the application's engine.
- **Service:** ``mcp_tool(name, ...)``. The call goes to the tool service
  (``flowgentic.tools.service``), one process that calls MCP tools for many
  agent processes through a supervised pool of clients
  (``flowgentic.tools.mcp``).

The declarations are framework-neutral; each framework's adapter turns them
into its own tools (``flowgentic.langgraph.bind_tools`` for LangGraph).

A ``ToolRuntime`` runs them for one agent process. A task or service call is
keyed by the agent, the framework's step that made the call (its ``scope``,
LangGraph's task id) and the tool call's id, so a call made again with the
same key, as a framework does when it resumes an agent, gets the first call's
result and runs nothing, while a later step that reuses a call id, as a model
server that numbers its calls per message does, makes a new call. The runtime
keeps the outcomes of the last ``retained`` finished tasks; the tool service
keeps its own answers.

A task may also **suspend** its agent: the agent's state is saved and dropped
while the task runs, and the agent host resumes it when the task ends.

When tasks reach RHAPSODY or AsyncFlow is a policy: ``TaskOrder``.
``Immediate()`` submits each task at once. Another policy can hold tasks and
release them in an order of its choosing, such as a campaign's priorities, or
refuse them, as a budget does.

Each task ends in a ``TaskRecord``, its decision record: the agent and the tool
call that asked for it, its uid in RHAPSODY or AsyncFlow, and when the agent
committed to it, when it was submitted, started and ended.

Task tools need the ``rhapsody`` extra when they run on a session, and the
``asyncflow`` extra on an engine; declaring them, MCP tools and the agent
processes' side of the tool service need no extra.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from flowgentic import _agents, _rhapsody, _telemetry
from flowgentic._agents import TaskCancelled

__all__ = [
	"Immediate",
	"McpTool",
	"TaskCancelled",
	"TaskInfo",
	"TaskOrder",
	"TaskRecord",
	"TaskTool",
	"ToolRuntime",
	"mcp_tool",
	"task_tool",
]

if TYPE_CHECKING:
	from radical.asyncflow import WorkflowEngine

log = logging.getLogger(__name__)

# The fields a task tool may give a task on an AsyncFlow engine: its backend's
# name, and the backend's own fields, AsyncFlow's ``task_description``.
_ENGINE_FIELDS = frozenset({"backend", "task_backend_specific_kwargs"})

# What a task uid keeps as it is; every other character is escaped.
_ESCAPED = re.compile(r"[^A-Za-z0-9-]")


def _escape(match: re.Match) -> str:
	c = match.group()
	if c == "/":
		return "."
	if c == "_":
		return "__"
	return f"_x{ord(c):x}_"


def _read_outcome(job: asyncio.Future) -> None:
	# The runtime reports each task's outcome itself: in its records and spans,
	# and to each call that asks. A failure that no call read, as when its agent
	# was stopped, is no error for asyncio to log.
	if not job.cancelled():
		job.exception()


def _key_part(value: str) -> str:
	# A part of a key holds no "/", so the parts of a key are always the same.
	return str(value).replace("%", "%25").replace("/", "%2F")


# --- declarations ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskTool:
	"""A tool that runs ``fn`` as a function task, on a RHAPSODY session or an
	AsyncFlow engine (``ToolRuntime``).

	``suspend``: whether the agent suspends while the task runs: a bool, or a
	function of the agent's id and the call's arguments. It is asked again when
	the agent resumes, so it must give the same answer for the same call.
	``task``: fields added to the task's description. ``backend`` names the
	backend to run on, of the session or of the engine, and
	``task_backend_specific_kwargs`` holds that backend's own fields, such as
	resources. On a session any field of a RHAPSODY ``ComputeTask`` may be
	given; on an engine only these two.

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
	top-level function. It may be ``async def``: the worker runs it on an event
	loop of its own, to its end. See ``TaskTool`` for the options, such as
	``backend="compute"``."""

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
	"""One task, for listeners: its decision record.

	``info`` names the agent (``info.agent_id``) and the tool call that asked
	for the task (``info.call_id``). ``outcome`` is ``ok``, ``error`` (the task
	failed), ``refused`` (the order policy refused it) or ``cancelled``; with
	the task's value or error.

	Times are ``time.time()``, in the agent's process, in this order.
	``t_commit``: when the agent committed to the call: when the model reply
	that made the call (by its id) reached the agent, if the model runs through
	a ``flowgentic.fleet.Fleet`` and the agent in an ``AgentHost``; otherwise
	``t_submit``. If one agent's parallel branches get replies that reuse a
	call id, each such call is dated by the latest of them. ``t_submit``: when
	the runtime got the call. ``t_admit``: when
	the order policy admitted the task and the runtime submitted it to RHAPSODY
	or AsyncFlow; ``None`` if it never was. ``t_start``: on an engine, when
	the backend reported the task running, as AsyncFlow's ``TaskStarted``
	event in the telemetry the runtime was given says, if the event arrived
	before the task's record was made; otherwise ``None``, as on a session.
	RHAPSODY's thread and process pools report a task running when they hand
	it to the pool, which runs it once a worker is free. ``t_finish``: on an engine, when
	the agent's process saw AsyncFlow resolve the task, one pass of the event
	loop after AsyncFlow's own end event; ``None`` on a session. ``t_end``: when
	the runtime saw the task end, before its agent is told.

	``uid``: the task's uid in AsyncFlow, which sets it when the runtime calls
	the task, or in RHAPSODY (``ToolRuntime.uid(info.key)``), once the session
	has accepted the task; ``None`` if neither has it. It joins the record to
	AsyncFlow's telemetry: a start that arrived too late is there. ``backend``:
	the backend the task was for: the tool's, else the runtime's, else, on an
	engine, the engine's default, and on a session the one the session sent it
	to, once it accepted the task.
	"""

	info: TaskInfo
	t_submit: float
	t_admit: float | None
	t_end: float
	outcome: str
	value: Any = None
	error: str | None = None
	uid: str | None = None
	backend: str | None = None
	t_commit: float | None = None
	t_start: float | None = None
	t_finish: float | None = None

	@property
	def ok(self) -> bool:
		"""Whether the task returned a value."""
		return self.outcome == "ok"


class TaskOrder(Protocol):
	"""When, and whether, tasks reach RHAPSODY or AsyncFlow."""

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

	Task tools run on ``session``, a RHAPSODY ``Session``, or on ``engine``, an
	AsyncFlow ``WorkflowEngine``: the application's own, so that the tasks its
	agents ask for are tasks of the application, beside those of its workflow.
	Not both. ``backend``: the name of the session's or the engine's backend
	that task tools run on, unless a tool names its own; by default the
	session's or the engine's default. ``telemetry``: on an engine, the
	telemetry ``engine.start_telemetry()`` returned, from which each task's
	record takes its start. ``service``: a started
	``flowgentic.tools.service.ToolServiceClient``, for MCP tools. ``order``:
	when tasks are submitted, ``Immediate()`` by default. ``retained``: how
	many ended tasks are kept, for repeat safety.

	On an engine, AsyncFlow 0.7.0 keeps each task, with its value, or its exception
	and the frames it passed through, until the engine shuts down; a task
	still running then ends with ``TaskCancelled``, as on a session that
	closes.

	A runtime set up wrong for a tool, such as one naming a backend the session
	or engine lacks, is refused when the tool is bound (``check``), and with
	``RuntimeError`` when the tool is called.

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
		engine: WorkflowEngine | None = None,
		backend: str | None = None,
		telemetry: Any = None,
		order: TaskOrder | None = None,
		retained: int = 100_000,
	) -> None:
		if retained < 1:
			raise ValueError("retained must be at least 1")
		if session is not None and engine is not None:
			raise ValueError(
				"a ToolRuntime runs task tools on a RHAPSODY session or on an "
				"AsyncFlow engine, not both"
			)
		if telemetry is not None and engine is None:
			raise ValueError(
				"telemetry gives the start of tasks on an AsyncFlow engine: a "
				"ToolRuntime with telemetry needs an engine"
			)
		self._starts = None
		if engine is not None:
			from flowgentic import _asyncflow

			if backend is not None:
				_asyncflow.check_backend(engine, backend)
			if telemetry is not None:
				self._starts = _asyncflow.starts(telemetry)
		elif session is not None and backend is not None:
			_rhapsody.check_backend(session, backend)
		self.session = session
		self.service = service
		self.engine = engine
		self.backend = backend
		self.telemetry = telemetry
		self.order = order or Immediate()
		self.retained = retained
		self._on_engine: dict[tuple[Callable[..., Any], str | None], Callable] = {}
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
	def key(agent_id: str, call_id: str, scope: str = "") -> str:
		"""The key of one tool call: ``agent/scope/call``, or ``agent/call``
		without a scope.

		``scope`` names the framework's step that made the call, within which
		the call's id is unique and which is the same when the step runs again
		on resume: LangGraph's task id. A ``/`` or ``%`` in a part is written
		``%2F`` or ``%25``, so different calls never share a key."""
		parts = (agent_id, scope, call_id) if scope else (agent_id, call_id)
		return "/".join(_key_part(p) for p in parts)

	@staticmethod
	def uid(key: str) -> str:
		"""The RHAPSODY task uid of a key: ``flowgentic.`` and the key, with
		``/`` written ``.``, ``_`` written ``__`` and any character other than
		a letter, a digit or ``-`` written ``_x<hex>_``. Different keys never
		share a uid."""
		return "flowgentic." + _ESCAPED.sub(_escape, key)

	def submit(
		self,
		tool: TaskTool,
		agent_id: str,
		call_id: str,
		args: dict[str, Any],
		*,
		scope: str = "",
	) -> asyncio.Future:
		"""The future of the task for this call: a new task, or the one already
		submitted under the same key. ``scope``: see ``key``.

		A failed task keeps its exception for as long as it is kept. Read it
		with ``exception()`` rather than raise it in a framework's code, whose
		frames its traceback would then keep."""
		key = self.key(agent_id, call_id, scope)
		job = self._jobs.get(key)
		if job is None:
			# Dropped from the runtime's ended tasks, but still registered by the
			# agent asking: an agent resuming after other tasks ended finds it.
			job = _agents.registered(key)
		if job is not None:
			self.repeats += 1
			return job
		t_submit = time.time()
		if self.session is None and self.engine is None:
			raise RuntimeError(
				"task tools need a ToolRuntime with a RHAPSODY session or an "
				"AsyncFlow engine"
			)
		try:
			fields, on_engine = self._placed(tool)
		except ValueError as exc:
			# Set up wrong, as without a session: the agent fails, rather than
			# tell its model that the call failed.
			raise RuntimeError(f"task tool {tool.name}: {exc}") from exc
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
		committed = _agents.reply_time(agent_id, call_id)
		within = None
		if self.engine is not None:
			from flowgentic import _asyncflow

			# The workflow scope or block the caller runs in, which the task's
			# own context below drops: AsyncFlow labels the task with it, and
			# cancels it with the block.
			within = _asyncflow.workflow(self.engine)
		# An asyncio task keeps the context it was created in, and the runtime
		# keeps ended tasks: in the caller's context, a kept task would keep the
		# framework's run, and with it the agent's whole state.
		job = asyncio.get_running_loop().create_task(
			self._run(tool, info, fields, on_engine, t_submit, committed, within),
			context=_agents.task_context(),
		)
		self._jobs[key] = job
		if _agents.current_agent() is not None:
			_agents.register_pending(key, job)  # released when the agent ends
		# Until its order admits it, a task that no call waits for any more is
		# withdrawn: it never reaches RHAPSODY.
		_agents.withdrawable(job, lambda: self._forget(key, job, cancel=True))
		job.add_done_callback(_read_outcome)
		self.submitted += 1
		return job

	def check(self, tool: TaskTool) -> None:
		"""Raise ``ValueError`` if this runtime cannot run ``tool``'s tasks: a
		backend its session or engine lacks, or, on an engine, a field AsyncFlow
		cannot take. A framework's adapter calls it when it binds the tool
		(``flowgentic.langgraph.bind_tools`` does), so that a runtime set up
		wrong fails before any agent runs."""
		self._placed(tool)

	def _placed(
		self, tool: TaskTool
	) -> tuple[
		dict[str, Any],
		Callable[..., asyncio.Future] | None,
	]:
		"""The fields of ``tool``'s tasks, the runtime's backend included, and on
		an engine how to submit them: ``tool``'s function, registered once per
		backend. ``ValueError`` as ``check`` says."""
		fields = dict(tool.task)
		own = fields.pop("backend", None)  # None names no backend
		backend = own if own is not None else self.backend
		if backend is not None:
			fields["backend"] = backend
		if fields.get("task_backend_specific_kwargs", {}) is None:
			del fields["task_backend_specific_kwargs"]  # as none given
		specific = fields.get("task_backend_specific_kwargs", {})
		if not isinstance(specific, dict):
			raise ValueError(
				"task_backend_specific_kwargs holds the backend's own fields: a "
				f"dict, not {type(specific).__name__}"
			)
		if self.engine is None:
			if self.session is not None and own is not None:
				_rhapsody.check_backend(self.session, own)  # the runtime's: at init
			return fields, None
		from flowgentic import _asyncflow

		extra = set(fields) - _ENGINE_FIELDS
		if extra:
			raise ValueError(
				f"a task on an AsyncFlow engine takes the fields "
				f"{sorted(_ENGINE_FIELDS)} only, not {sorted(extra)}"
			)
		submit = self._on_engine.get((tool.fn, backend))
		if submit is None:
			submit = _asyncflow.function_task(self.engine, tool.fn, backend=backend)
			self._on_engine[(tool.fn, backend)] = submit
		return fields, submit

	def _forget(self, key: str, job: asyncio.Future, *, cancel: bool = False) -> None:
		"""Forget a task that never reached RHAPSODY, so that the call made
		again submits it afresh; ``cancel`` it too."""
		if self._jobs.get(key) is job:
			del self._jobs[key]
		if _agents.pending(key) is job or _agents.registered(key) is job:
			_agents.forget_pending(key)
		if cancel:
			job.cancel()

	async def _run(
		self,
		tool: TaskTool,
		info: TaskInfo,
		fields: dict[str, Any],
		on_engine: Callable[..., asyncio.Future] | None,
		t_submit: float,
		committed: float | None,
		within: str | None,
	) -> Any:
		t = time.monotonic()
		t_admit: float | None = None
		value: Any = None
		error: str | None = None
		outcome = "error"
		uid: str | None = None
		backend: str | None = fields.get("backend")
		finish = None
		if on_engine is not None:
			from flowgentic import _asyncflow

			backend = backend or _asyncflow.default_backend(self.engine)
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
				_agents.withdrawable(asyncio.current_task(), None)
				try:
					if on_engine is None:
						named = self.uid(info.key)

						def submitted(sent_to: str | None) -> None:
							nonlocal uid, backend
							uid = named  # once the session has accepted the task
							backend = sent_to or backend

						value = await _rhapsody.run_function(
							self.session,
							tool.fn,
							info.kwargs,
							uid=named,
							on_submitted=submitted,
							**fields,
						)
					else:
						future = on_engine(
							info.kwargs,
							fields.get("task_backend_specific_kwargs") or {},
							within,
						)
						uid = _asyncflow.uid(future)
						finish = _asyncflow.Finish(future)
						if self._starts is not None:
							self._starts.add(uid)
						value = await _asyncflow.result(future)
				finally:
					self.order.done(info)
				outcome = "ok"
				return value
			except asyncio.CancelledError:
				outcome = "cancelled"
				error = "CancelledError"
				if t_admit is None:  # withdrawn, or cancelled before admission
					self._forget(info.key, asyncio.current_task())
				raise
			except TaskCancelled as exc:
				outcome = "cancelled"
				error = f"TaskCancelled: {exc}"
				raise
			except Exception as exc:
				if outcome == "error":
					self.failed += 1
				error = f"{type(exc).__name__}: {exc}"
				span.set_attribute(_telemetry.ERROR_TYPE, type(exc).__qualname__)
				raise
			finally:
				_agents.withdrawable(asyncio.current_task(), None)
				span.set_attribute(_telemetry.TOOL_OUTCOME, outcome)
				if t_admit is not None or outcome != "cancelled":
					self._ended_add(info.key)
				t_start = None
				if self._starts is not None and uid is not None:
					t_start = self._starts.pop(uid)
				self._tell(
					TaskRecord(
						info,
						t_submit,
						t_admit,
						time.time(),
						outcome,
						value,
						error,
						uid=uid,
						backend=backend,
						t_commit=t_submit if committed is None else committed,
						t_start=t_start,
						t_finish=None if finish is None else finish.time,
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
		self,
		tool: McpTool,
		agent_id: str,
		call_id: str,
		args: dict[str, Any],
		*,
		scope: str = "",
	) -> Any:
		"""Call an MCP tool through the tool service, with the call's key as its
		uid; a repeated key gets the service's kept answer. ``scope``: see
		``key``. ``RuntimeError`` means the runtime is set up wrong: no service
		client, or one not started."""
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
					self.key(agent_id, call_id, scope),
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
