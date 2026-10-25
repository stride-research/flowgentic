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
"""

import functools
from typing import Any, Awaitable, Callable

from radical.asyncflow import WorkflowEngine

from flowgentic import _telemetry

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
	"""Return the AsyncFlow uid of a submitted task, once AsyncFlow knows it.

	AsyncFlow 0.6.0 records it on the future when it registers the task;
	later releases set ``future.uid`` when the task is called.
	"""
	task_uid = getattr(future, "uid", None)
	if task_uid is None:
		description = getattr(future, "task", None)
		if isinstance(description, dict):
			task_uid = description.get("uid")
	return task_uid


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
