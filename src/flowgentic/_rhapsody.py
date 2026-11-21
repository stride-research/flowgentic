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

import asyncio
import inspect
import weakref
from collections.abc import Callable
from typing import Any

from flowgentic import _telemetry
from flowgentic._agents import TaskCancelled

# A task's state once RHAPSODY has cancelled it, in every backend's mapping.
_CANCELED = "CANCELED"


class _Session:
	"""What Flowgentic knows of a RHAPSODY session: whether it has closed, and
	the runs still waiting on its tasks."""

	__slots__ = ("closed", "waits")

	def __init__(self) -> None:
		self.closed = False
		self.waits: set[asyncio.Future] = set()

	def wake(self) -> None:
		self.closed = True
		for wait in list(self.waits):
			if not wait.done():
				wait.set_result(None)


_sessions: weakref.WeakKeyDictionary[Any, _Session] = weakref.WeakKeyDictionary()


def _session(session: Any) -> _Session:
	"""The session's record, made on first use.

	A RHAPSODY 0.7 session that closes resolves the futures of the tasks it
	drops on some backends and not on others, such as the thread-pool one, so a
	run could wait forever on a task that will never end. The first use of a
	session wraps its ``close``: once the session has closed, every run still
	waiting on one of its tasks is told."""
	try:
		known = _sessions.get(session)
	except TypeError:  # a session that cannot be weakly referenced: not watched
		return _Session()
	if known is None:
		known = _sessions[session] = _Session()
		close = getattr(session, "close", None)
		if close is not None:

			async def close_and_tell(*args: Any, **kwargs: Any) -> Any:
				try:
					return await close(*args, **kwargs)
				finally:
					known.wake()

			session.close = close_and_tell
	return known


def _call_in_context(
	func: Callable[..., Any], carrier: dict[str, str], kwargs: dict[str, Any]
) -> Any:
	"""Run ``func`` in the caller's trace context; runs in a RHAPSODY worker.

	An ``async def`` function's coroutine is run to its end here, as RHAPSODY's
	backends run one: they see only this wrapper, which is not async."""
	token = _telemetry.attach_context(carrier)
	try:
		value = func(**kwargs)
		if inspect.iscoroutine(value):
			value = asyncio.run(value)
		return value
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


def check_backend(session: Any, backend: str) -> None:
	"""Raise ``ValueError`` unless ``backend`` names one of the session's
	backends that run tasks."""
	names = [
		name
		for name, known in session.backends.items()
		if hasattr(known, "submit_tasks")
	]
	if backend not in names:
		raise ValueError(
			f"the RHAPSODY session has no backend {backend!r}: its backends are "
			+ ", ".join(repr(name) for name in names)
		)


async def run_function(
	session: Any,
	func: Callable[..., Any],
	kwargs: dict[str, Any],
	*,
	uid: str,
	on_submitted: Callable[[str | None], None] | None = None,
	**task: Any,
) -> Any:
	"""Run ``func(**kwargs)`` as one RHAPSODY function task named ``uid``, and
	return its value. A failure raises the task's exception. ``task`` adds
	fields to the task's description, such as resources or a backend's name.
	``on_submitted(backend)`` is called once the session has accepted the
	task, with the name of the backend it sent the task to.

	``TaskCancelled`` is raised if RHAPSODY cancels the task, or if the session
	is closed or closes before the task ends.

	A RHAPSODY session keeps each task's description for its life, and the
	description holds the task's arguments, its result, the backend's text of
	it and its exception. Once the task has ended and its outcome is read, they
	are dropped from the description: the caller has them."""
	compute_task = _compute_task()
	known = _session(session)
	if known.closed:
		raise TaskCancelled("the RHAPSODY session is closed")
	description = compute_task(
		function=_call_in_context,
		args=(func, _telemetry.inject_context(), kwargs),
		uid=uid,
		**task,
	)
	(future,) = await session.submit_tasks([description])
	if on_submitted is not None:
		on_submitted(description.get("backend"))  # the session routed it
	closed = asyncio.get_running_loop().create_future()
	known.waits.add(closed)
	try:
		try:
			await asyncio.wait((future, closed), return_when=asyncio.FIRST_COMPLETED)
		except asyncio.CancelledError:
			future.cancel()
			raise
		if not future.done():
			raise TaskCancelled("the RHAPSODY session closed before the task ended")
		done = future.result()
		if done.get("state") == _CANCELED:
			raise TaskCancelled("RHAPSODY cancelled the task")
		return done["return_value"]
	finally:
		known.waits.discard(closed)
		if future.done() and not future.cancelled():
			description.update(
				args=(), return_value=None, stdout=None, stderr=None, exception=None
			)
			# The future bound to the description holds the task's exception,
			# whose traceback reaches the caller's frames.
			description._future = None
