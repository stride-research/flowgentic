"""Flowgentic's one place for submitting work to RHAPSODY.

Only this module imports RHAPSODY, as ``_asyncflow`` is the only one that
imports AsyncFlow. Flowgentic never creates a RHAPSODY backend: the caller
creates a ``Session`` on whichever backend fits its machine (a thread pool,
Dragon, Dask) and hands it over.

A task runs in the trace context of its submitter: RHAPSODY's workers run a
function in another thread or process, where the caller's OpenTelemetry
context does not follow, so the context travels with the task as a W3C carrier.

RHAPSODY is an extra, imported when the first task is submitted: without it,
that submission raises an error that names the extra to install.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flowgentic import _telemetry


def _call_in_context(
	func: Callable[..., Any], carrier: dict[str, str], kwargs: dict[str, Any]
) -> Any:
	"""Run ``func`` in the caller's trace context; runs in a RHAPSODY worker."""
	token = _telemetry.attach_context(carrier)
	try:
		return func(**kwargs)
	finally:
		_telemetry.detach_context(token)


def _compute_task() -> type:
	try:
		from rhapsody import ComputeTask
	except ModuleNotFoundError as exc:
		if exc.name != "rhapsody":
			raise
		raise ModuleNotFoundError(
			"Flowgentic's task tools need RHAPSODY: install flowgentic[rhapsody]",
			name=exc.name,
		) from exc
	return ComputeTask


async def run_function(
	session: Any,
	func: Callable[..., Any],
	kwargs: dict[str, Any],
	*,
	uid: str,
	**task: Any,
) -> Any:
	"""Run ``func(**kwargs)`` as one RHAPSODY function task named ``uid``, and
	return its value. A failure raises the task's exception. ``task`` adds
	fields to the task's description, such as resources or a backend's name."""
	description = _compute_task()(
		function=_call_in_context,
		args=(func, _telemetry.inject_context(), kwargs),
		uid=uid,
		**task,
	)
	(future,) = await session.submit_tasks([description])
	done = await future
	return done["return_value"]
