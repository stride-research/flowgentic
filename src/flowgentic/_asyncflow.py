"""Flowgentic's one place for submitting work to AsyncFlow.

Every other module creates and shuts down engines, and registers tasks,
services and blocks, through these functions; only this module imports
AsyncFlow, except for type annotations under ``TYPE_CHECKING``. A new kind of
submission, or a path that submits without a workflow, is added here.

Each function returns what AsyncFlow returns: calling a registered task,
service or block submits it and returns its future.
"""

from typing import Any, Awaitable, Callable

from radical.asyncflow import WorkflowEngine

Submitter = Callable[..., Awaitable[Any]]


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
	return engine.function_task(func, backend=backend, capture_stdio=capture_stdio)


def service(
	engine: WorkflowEngine,
	func: Callable[..., Any],
	*,
	backend: str | None = None,
	capture_stdio: bool = False,
) -> Submitter:
	"""Register a service task, which keeps running once started."""
	return engine.function_task(
		func, service=True, backend=backend, capture_stdio=capture_stdio
	)


def block(engine: WorkflowEngine, func: Callable[..., Any]) -> Submitter:
	"""Register a block, which runs in the workflow and may submit its own tasks."""
	return engine.block(func)
