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
``result``. ``Finish`` stamps when a task ended, and ``starts`` reads when tasks
started from the telemetry an application has started on its engine.

AsyncFlow is an extra: without it, importing this module raises an error that
names the extra to install.
"""

import asyncio
import contextvars
import functools
import inspect
import time
import weakref
from importlib.metadata import PackageNotFoundError, version
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
from flowgentic._agents import TaskCancelled

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


def check_engine(engine: WorkflowEngine) -> None:
	"""Raise ``RuntimeError`` unless the engine has the one private name task
	tools read of it: its workflow scope variable (see ``workflow``)."""
	if not isinstance(
		getattr(engine, "_workflow_id_ctx", None), contextvars.ContextVar
	):
		try:
			installed = version("radical-asyncflow")
		except PackageNotFoundError:  # a source checkout on the path
			installed = "of unknown version"
		raise RuntimeError(
			"Flowgentic's task tools read the engine's workflow scope "
			f"(WorkflowEngine._workflow_id_ctx), which radical-asyncflow "
			f"{installed} lacks: Flowgentic supports radical-asyncflow>=0.7,<0.8"
		)


def check_loop(engine: WorkflowEngine) -> None:
	"""Raise ``RuntimeError`` unless the caller runs on the engine's event loop,
	where AsyncFlow registers tasks. An AsyncFlow task's function, such as an
	agent ``bind_agent`` runs, runs on a loop of its own in a pool thread: a
	task submitted from there would never be registered."""
	if asyncio.get_running_loop() is not engine.loop:
		raise RuntimeError(
			"a task tool on an AsyncFlow engine must be called on the engine's "
			"event loop: run the agent in a coroutine, a block or an agent host "
			"on that loop, not inside an AsyncFlow task"
		)


def check_telemetry(engine: WorkflowEngine, telemetry: Any) -> None:
	"""Raise ``ValueError`` unless ``telemetry`` is the engine's own, the one
	``engine.start_telemetry()`` returned: the engine emits to no other."""
	session = getattr(telemetry, "session_id", None)
	if session != engine.uid:
		raise ValueError(
			f"the telemetry belongs to another engine ({session!r}, not "
			f"{engine.uid!r}): pass the one engine.start_telemetry() returned"
		)


def workflow(engine: WorkflowEngine) -> str | None:
	"""The id of the workflow scope or block the caller runs in, on this
	engine; ``None`` outside both.

	AsyncFlow labels each task with it, and makes a task called in a block one
	of the block's, cancelled with it. AsyncFlow 0.7 keeps it in a context
	variable of the engine's, with no public accessor: this is the one place
	that reads it."""
	return engine._workflow_id_ctx.get()


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
) -> Callable[..., asyncio.Future]:
	"""Register ``func`` as a function task, on the named backend or the
	default one. ``ValueError`` if no backend has that name.

	Each call ``submit(kwargs, description)`` submits one task that runs
	``func(**kwargs)`` in the caller's trace context, with ``description`` as
	AsyncFlow's ``task_description`` (the backend's own fields), and returns
	the task's future; ``uid(future)`` is set from then on. ``func`` may be
	``async def``: its coroutine is run to its end. Its arguments travel as
	one dictionary, so that none is read as one of AsyncFlow's own call
	keywords, such as ``task_description`` or ``workflow_id``.

	``submit(kwargs, description, within)`` submits the task as if called in
	the workflow scope or block ``within`` (``workflow`` read where the task
	was asked for): AsyncFlow labels the task with it, and a block's
	cancellation cancels the task.
	"""
	registered = engine.function_task(
		_in_callers_context(_with_kwargs(func)), backend=backend
	)
	submit = _passing_context(registered)
	scope = engine._workflow_id_ctx

	def call(
		kwargs: dict[str, Any],
		description: dict[str, Any],
		within: str | None = None,
	) -> asyncio.Future:
		# AsyncFlow registers the task in a task of its own, which copies this
		# context: the scope set here is the one it reads.
		token = scope.set(within)
		try:
			return submit(kwargs, task_description=description)
		finally:
			scope.reset(token)

	return call


async def result(future: asyncio.Future) -> Any:
	"""Wait for a task submitted with ``function_task``, and return its value.

	A failure raises the task's exception. ``TaskCancelled`` is raised if
	AsyncFlow cancelled the task, as it does to the tasks still running when
	the engine shuts down, and to a task called after. Cancelling the wait
	cancels the task, on its backend if it runs there."""
	try:
		await asyncio.wait((future,))
	except asyncio.CancelledError:
		future.cancel()
		raise
	if future.cancelled():
		raise TaskCancelled("AsyncFlow cancelled the task")
	return future.result()


class Finish:
	"""When a task submitted with ``function_task`` ended, in this process.

	``time`` is ``time.time()`` on the event loop's first pass after AsyncFlow
	resolved the task's future, which it does as the backend reports the
	task's end, just before it emits the task's end event; ``None`` until then.
	It needs no telemetry, and no event."""

	__slots__ = ("time",)

	def __init__(self, future: asyncio.Future) -> None:
		self.time: float | None = None
		future.add_done_callback(self._stamp)

	def _stamp(self, future: asyncio.Future) -> None:
		self.time = time.time()


class Starts:
	"""When tasks started, from AsyncFlow's ``TaskStarted`` events.

	For each task added, the ``event_time`` of its first ``TaskStarted`` event;
	``None`` until the event arrives. The telemetry delivers events in batches,
	after the fact: under a burst of events, a task can end before its start
	event arrives. A backend that never reports a task running, such as
	AsyncFlow's own ``LocalExecutionBackend``, gives no start."""

	__slots__ = ("_starts",)

	def __init__(self) -> None:
		self._starts: dict[str, float | None] = {}

	def add(self, task_uid: str) -> None:
		"""Watch the task ``task_uid`` from now on."""
		self._starts[task_uid] = None

	def pop(self, task_uid: str) -> float | None:
		"""The task's start, if its event has arrived, and stop watching it."""
		return self._starts.pop(task_uid, None)

	def __call__(self, event: Any) -> None:
		task_uid = event.task_id
		if (
			event.event_type == "TaskStarted"
			and task_uid in self._starts
			and self._starts[task_uid] is None
		):
			self._starts[task_uid] = event.event_time


_watched: weakref.WeakKeyDictionary[Any, Starts] = weakref.WeakKeyDictionary()


def starts(telemetry: Any) -> Starts:
	"""The ``Starts`` of a telemetry, the one ``engine.start_telemetry()``
	returns: subscribed to it on first use, then shared."""
	known = _watched.get(telemetry)
	if known is None:
		known = _watched[telemetry] = Starts()
		telemetry.subscribe(known)
	return known


def _with_kwargs(func: Callable[..., Any]) -> Callable[..., Awaitable[Any]]:
	"""An async function that runs ``func(**kwargs)``, given ``kwargs``: named
	after ``func``, which is how AsyncFlow and its telemetry know the task.

	AsyncFlow runs only ``async def`` tasks, each on an event loop of the
	task's own in the backend's worker. ``func`` is called with that loop
	hidden, so that a sync ``func`` runs as on a RHAPSODY session, with no
	event loop running in its thread, and may run one itself, as
	``asyncio.run`` does. An ``async def`` one only makes its coroutine then,
	which runs on the task's loop."""

	@functools.wraps(func)
	async def run(kwargs: dict[str, Any]) -> Any:
		loop = asyncio.get_running_loop()
		asyncio._set_running_loop(None)
		try:
			value = func(**kwargs)
		finally:
			asyncio._set_running_loop(loop)
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
