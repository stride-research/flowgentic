"""Read AsyncFlow's records of a test's tasks without outliving them.

AsyncFlow may release a finished component's records: from ``flow.components``
and from ``LocalExecutionBackend.tasks`` (radical-cybertools/radical.asyncflow#128).
A test that reads them after its tasks ended then finds nothing. These helpers
record what the tests need while AsyncFlow still holds it.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any


def record_submissions(*backends: Any) -> dict[str, list[dict[str, Any]]]:
    """Record the tasks the engine submits to each backend, by backend name.

    Wraps each backend's ``submit_tasks``, which an AsyncFlow engine calls to
    place a batch of tasks on that backend. Each list holds the task descriptions
    in the order the backend received them.
    """
    submitted: dict[str, list[dict[str, Any]]] = {}
    for backend in backends:
        received = submitted.setdefault(backend.name, [])
        submit_tasks = backend.submit_tasks

        async def recording(
            tasks: list[dict[str, Any]],
            *args: Any,
            received: list[dict[str, Any]] = received,
            submit_tasks: Any = submit_tasks,
            **kwargs: Any,
        ) -> Any:
            received.extend(tasks)
            return await submit_tasks(tasks, *args, **kwargs)

        backend.submit_tasks = recording
    return submitted


@contextlib.asynccontextmanager
async def registered(flow: Any) -> AsyncIterator[dict[str, dict[str, Any]]]:
    """Collect ``flow``'s record of each component registered while the block runs.

    Yields a mapping that fills as the block runs: uid to the record AsyncFlow
    keeps in ``flow.components`` (``type``, ``future`` and ``description``). It
    holds the components registered on entry, such as a resident service, and
    those registered inside the block. A component stays registered until its
    task has run and its dependents were notified, which takes more than one turn
    of the event loop; the records are read on every turn.
    """
    records: dict[str, dict[str, Any]] = {}

    def collect() -> None:
        for uid, record in list(flow.components.items()):
            records.setdefault(uid, record)

    async def watch() -> None:
        while True:
            collect()
            await asyncio.sleep(0)

    collect()
    watcher = asyncio.create_task(watch())
    try:
        yield records
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        collect()
