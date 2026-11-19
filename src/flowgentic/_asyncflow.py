"""Flowgentic's one place for submitting work to AsyncFlow.

Every other module creates and shuts down engines, and registers tasks,
services and blocks, through these functions; only this module imports
AsyncFlow, except for type annotations under ``TYPE_CHECKING``. A new kind of
submission, or a path that submits without a workflow, is added here.

Each function returns what AsyncFlow returns: calling a registered task,
service or block submits it and returns its future.

A task or service runs in the trace context of its caller. AsyncFlow's
backends run a function in a pool thread or another process, where the
caller's OpenTelemetry context does not follow, so the submitter passes it
along as a W3C carrier and the task attaches it.

Task tools reach an application's engine through ``function_task`` and
``result``; ``times`` reads their start and end from the engine's telemetry.

AsyncFlow is an extra: without it, importing this module raises an error that
names the extra to install.
"""

import asyncio
import functools
import inspect
import weakref
from typing import Any, Awaitable, Callable

try:
	from radical.asyncflow import WorkflowEngine
except ModuleNotFoundError as exc:
	if exc.name not in {"radical", "radical.asyncflow"}:
		raise
	raise ModuleNotFoundError(
		"Flowgentic's AsyncFlow path needs AsyncFlow: install flowgentic[asyncflow]",
		name=exc.name,
	) from exc

from flowgentic import _telemetry
from flowgentic._rhapsody import TaskCancelled

Submitter = Callable[..., Awaitable[Any]]

# The keyword that carries the caller's trace context into a task
_TRACE_CONTEXT = "_flowgentic_trace_context"


async def open_engine(backend: object) -> WorkflowEngine:
	"""Start an engine on one backend, or on a list of named backends."""
	return await WorkflowEngine.create(backend=backend)


async def close_engine(engine: WorkflowEngine) -> None:
	"""Shut down an engine and its backends."""
	await engine.shutdown()


def task(
	engine: WorkflowEngine,
	func: Callable[..., Any],
	*,
	backend: str | None = None,
	capture_stdio: bool = False,
) -> Submitter:
	"""Register a function task, on the named backend or the default one."""
	registered = engine.function_task(
		_in_callers_context(func), backend=backend, capture_stdio=capture_stdio
	)
	return _passing_context(registered)


def service(
	engine: WorkflowEngine,
	func: Callable[..., Any],
	*,
	backend: str | None = None,
	capture_stdio: bool = False,
) -> Submitter:
	"""Register a service task, which keeps running once started."""
	registered = engine.function_task(
		_in_callers_context(func),
		service=True,
		backend=backend,
		capture_stdio=capture_stdio,
	)
	return _passing_context(registered)


def block(engine: WorkflowEngine, func: Callable[..., Any]) -> Submitter:
	"""Register a block, which runs in the workflow and may submit its own tasks."""
	return engine.block(func)


def uid(future: object) -> str | None:
	"""Return the AsyncFlow uid of a submitted task.

	AsyncFlow 0.7 sets ``future.uid`` when the task is called; the task's
	description, set when it registers, holds it too.
	"""
	task_uid = getattr(future, "uid", None)
	if task_uid is None:
		description = getattr(future, "task", None)
		if isinstance(description, dict):
			task_uid = description.get("uid")
	return task_uid


# --- task tools on an application's engine -------------------------------------


def check_backend(engine: WorkflowEngine, backend: str) -> None:
	"""Raise ``ValueError`` unless ``backend`` names one of the engine's backends."""
	engine.function_task(backend=backend)  # checks the name, registers nothing


def default_backend(engine: WorkflowEngine) -> str:
	"""The name of the engine's default backend."""
	return engine.backend.name


def function_task(
	engine: WorkflowEngine,
	func: Callable[..., Any],
	*,
	backend: str | None = None,
) -> Callable[[dict[str, Any], dict[str, Any]], asyncio.Future]:
	"""Register ``func`` as a function task, on the named backend or the
	default one. ``ValueError`` if no backend has that name.

	Each call ``submit(kwargs, description)`` submits one task that runs
	``func(**kwargs)`` in the caller's trace context, with ``description`` as
	AsyncFlow's ``task_description`` (the backend's own fields), and returns
	the task's future; ``uid(future)`` is set from then on. ``func`` may be
	``async def``: its coroutine is run to its end. Its arguments travel as
	one dictionary, so that none is read as one of AsyncFlow's own call
	keywords, such as ``task_description`` or ``workflow_id``.
	"""
	registered = engine.function_task(
		_in_callers_context(_with_kwargs(func)), backend=backend
	)
	submit = _passing_context(registered)

	def call(kwargs: dict[str, Any], description: dict[str, Any]) -> asyncio.Future:
		return submit(kwargs, task_description=description)

	return call


async def result(future: asyncio.Future, times: "Times | None" = None) -> Any:
	"""Wait for a task submitted with ``function_task``, and return its value.

	A failure raises the task's exception. ``TaskCancelled`` is raised if
	AsyncFlow cancelled the task, as it does to the tasks still running when
	the engine shuts down, and to a task called after. Cancelling the wait
	cancels the task, on its backend if it runs there.

	With ``times``, the wait ends once the task's end event has reached them,
	if it can within one more pass of the event loop: AsyncFlow emits the event
	as it resolves the future, and its telemetry delivers it on the loop's
	next pass. A task cancelled by the engine has no end event."""
	try:
		await asyncio.wait((future,))
	except asyncio.CancelledError:
		future.cancel()
		raise
	if times is not None and not times.ended(uid(future)):
		await asyncio.sleep(0)
	if future.cancelled():
		raise TaskCancelled("AsyncFlow cancelled the task")
	return future.result()


# The events that end a task, in RHAPSODY's telemetry, which AsyncFlow emits
_END_EVENTS = frozenset({"TaskCompleted", "TaskFailed", "TaskCanceled"})


class Times:
	"""When AsyncFlow's telemetry says the tasks added here started and ended.

	For each task added, the ``event_time`` of its first ``TaskStarted`` event
	and of its first end event (``TaskCompleted``, ``TaskFailed`` or
	``TaskCanceled``), from the engine's telemetry; ``None`` until the event
	arrives. A backend that never reports a task running, such as AsyncFlow's
	own ``LocalExecutionBackend``, gives no start."""

	__slots__ = ("_times", "__weakref__")

	def __init__(self) -> None:
		self._times: dict[str, list[float | None]] = {}

	def add(self, task_uid: str) -> None:
		"""Watch the task ``task_uid`` from now on."""
		self._times[task_uid] = [None, None]

	def ended(self, task_uid: str | None) -> bool:
		"""Whether the task's end event has arrived."""
		times = self._times.get(task_uid)
		return times is not None and times[1] is not None

	def pop(self, task_uid: str) -> tuple[float | None, float | None]:
		"""The task's start and end, and stop watching it."""
		start, end = self._times.pop(task_uid, (None, None))
		return start, end

	def __call__(self, event: Any) -> None:
		times = self._times.get(event.task_id)
		if times is None:
			return
		kind = event.event_type
		if kind == "TaskStarted":
			if times[0] is None:
				times[0] = event.event_time
		elif kind in _END_EVENTS and times[1] is None:
			times[1] = event.event_time


_watched: weakref.WeakKeyDictionary[Any, Times] = weakref.WeakKeyDictionary()


def times(engine: WorkflowEngine) -> Times | None:
	"""The ``Times`` of the engine's telemetry, once ``engine.start_telemetry()``
	has run; ``None`` before. The first call for a telemetry subscribes to it.

	AsyncFlow 0.7 keeps the telemetry it started on the engine, privately: this
	is the one place that reads it."""
	telemetry = getattr(engine, "_telemetry", None)
	if telemetry is None:
		return None
	known = _watched.get(telemetry)
	if known is None:
		known = _watched[telemetry] = Times()
		telemetry.subscribe(known)
	return known


def _with_kwargs(func: Callable[..., Any]) -> Callable[..., Awaitable[Any]]:
	"""An async function that runs ``func(**kwargs)``, given ``kwargs``: named
	after ``func``, which is how AsyncFlow and its telemetry know the task."""

	@functools.wraps(func)
	async def run(kwargs: dict[str, Any]) -> Any:
		value = func(**kwargs)
		if inspect.isawaitable(value):
			value = await value
		return value

	# AsyncFlow reads the task's signature, for a task_description default:
	# the signature is run's, not func's.
	del run.__wrapped__
	return run


def _in_callers_context(func: Callable[..., Awaitable[Any]]) -> Callable[..., Any]:
	"""Wrap ``func`` to run in the trace context that its submitter passes."""

	@functools.wraps(func)
	async def run(*args: object, **kwargs: object) -> object:
		token = _telemetry.attach_context(kwargs.pop(_TRACE_CONTEXT, {}))
		try:
			return await func(*args, **kwargs)
		finally:
			_telemetry.detach_context(token)

	return run


def _passing_context(registered: Submitter) -> Submitter:
	"""Wrap a registered task so that each call passes the caller's context."""

	def submit(*args: object, **kwargs: object) -> Awaitable[Any]:
		kwargs[_TRACE_CONTEXT] = _telemetry.inject_context()
		return registered(*args, **kwargs)

	return submit
