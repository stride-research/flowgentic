"""Worker-side task instrumentation.

One decorator. It registers an ordinary AsyncFlow function task and adds a
single side effect inside the task body: before returning, the worker appends
one JSON line describing what the task cost.

Nothing on the parent side is wrapped or intercepted, and return values are
untouched. Every AsyncFlow capability behaves exactly as it does on an
undecorated task -- passing the returned future as another task's argument still
creates a real dependency edge, `@flow.block` still works, `task_description` is
still forwarded, cancellation still routes to the backend -- and decorated and
undecorated tasks mix freely in the same graph. That is the whole reason the
measurement lives in the worker rather than riding home in the return value: a
task's result stays its own, so no consumer anywhere has to know telemetry
exists.

Records go to one file per worker PROCESS, named by host and pid, so two
processes never write to the same file and no locking is needed anywhere.
Aggregation (`compute_stats()` below) is a group-by over the merged records, so
it does not care what order they were written in -- which is what keeps this
correct once tasks start completing out of order under stage 2/3 scheduling.

See docs/TELEMETRY.md (in the Track B demo directory) for the design rationale,
the file-count caveat at very large scale, and what is deliberately deferred.
"""

from __future__ import annotations

import functools
import json
import os

# Aliased: the public decorator below takes a parameter called `resource` (the
# "cpu"/"gpu" label), which would otherwise shadow this module inside it.
import resource as rusage
import socket
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

# ru_maxrss is kilobytes on Linux, bytes on macOS.
_RSS_TO_MB = 1 / 1024 if sys.platform != "darwin" else 1 / 1024**2

_config: dict[str, Any] = {"metrics_dir": None, "sys_paths": ()}


def configure(metrics_dir: str | Path, sys_paths: tuple[str, ...] = ()) -> None:
    """Set the per-run constants every decorated task needs.

    Call once in the driver, before any task is decorated. These values are read
    at decoration time and captured into each task's closure as plain strings,
    because a Dragon worker is a separate process that inherits neither this
    one's `sys.path` nor its module state.

    Args:
        metrics_dir: Directory workers append their JSONL records to. Created if
            absent. Point this at node-local disk and gather at job end if a run
            ever gets large enough for the file count to matter (see
            docs/TELEMETRY.md).
        sys_paths: Paths each worker must put on `sys.path` before importing the
            science code.
    """
    path = Path(metrics_dir)
    path.mkdir(parents=True, exist_ok=True)
    _config["metrics_dir"] = str(path)
    _config["sys_paths"] = tuple(str(p) for p in sys_paths)


def task(
    flow: Any,
    *,
    name: str,
    resource: str = "cpu",
    description: str = "",
    **asyncflow_kwargs: Any,
) -> Callable:
    """Register an instrumented AsyncFlow function task.

    The decorated function must be a nested function, defined inside the stage's
    own `run()`. A module-level function in a non-installed module pickles BY
    REFERENCE, which means the Dragon child has to import that module to
    deserialize the task, which it cannot do -- the task submits, the child dies
    before any of our code runs, and the future never resolves. Nested functions
    pickle by value regardless of module, which is why this works at all.
    `tests/test_no_module_capture.py` enforces it.

    Args:
        flow: The AsyncFlow `WorkflowEngine`.
        name: Label for this task's records, independent of the Python function
            name -- e.g. both the one-component and two-component dock-setup
            tasks record as "dock_setup" even though their functions are named
            differently. Required, not derived from `fn.__name__`, so renaming a
            task function never silently renames its metrics.
        resource: Resource pool this task belongs on, "cpu" or "gpu". Recorded
            on every record and used only for grouping. Nothing here reserves or
            pins anything.
        description: Free text about what this task does and how it behaves in
            the pipeline. Recorded on every record, for the stage-3 reasoner to
            read back.
        **asyncflow_kwargs: Forwarded verbatim to `flow.function_task`, so
            `task_description`, `backend`, `service`, `capture_stdio` and
            anything upstream adds later keep working untouched.

    Returns:
        The AsyncFlow-registered task. Calling it returns a real AsyncFlow
        future whose result is the decorated function's own return value,
        unchanged.

    Raises:
        RuntimeError: If `configure()` has not been called yet. Raised here, in
            the parent at decoration time, rather than inside a worker where it
            would surface as an unresolved future.
    """
    metrics_dir, sys_paths = _config["metrics_dir"], _config["sys_paths"]
    if metrics_dir is None:
        raise RuntimeError(
            "flowgentic.metrics.configure() must be called before decorating tasks"
        )

    def decorate(fn: Callable) -> Callable:
        # Bound to locals so the closure captures plain strings by value. Never
        # capture a module or a non-installed object here; see the note above.
        label, kind, detail = name, resource, description

        @flow.function_task(**asyncflow_kwargs)
        @functools.wraps(fn)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            # Worker side, in the Dragon child. These resolve from site-packages,
            # the only import path a child can count on.
            from flowgentic.metrics import _prepare, _snapshot, _write

            _prepare(sys_paths)
            before = _snapshot()
            try:
                return await fn(*args, **kwargs)
            finally:
                # sys.exc_info() inside a finally reports the exception currently
                # propagating, so a failed task still leaves a record without
                # this needing to catch and re-raise anything itself.
                exc = sys.exc_info()[1]
                _write(
                    metrics_dir,
                    label,
                    kind,
                    detail,
                    before,
                    _snapshot(),
                    f"{type(exc).__name__}: {exc}" if exc is not None else None,
                )

        return wrapped

    return decorate


# _prepare/_snapshot/_write look unused to a static checker: their only callers
# are the `from flowgentic.metrics import ...` inside `wrapped()` above, which
# exists specifically so the Dragon worker re-imports this installed module
# instead of inheriting a closure reference it couldn't resolve. Deliberate,
# not dead code -- see the module docstring and metrics.task()'s docstring.


def _prepare(sys_paths: tuple[str, ...]) -> None:
    """Put the science code on this worker's `sys.path`. Idempotent."""
    for path in sys_paths:
        if path not in sys.path:
            sys.path.insert(0, path)


def _snapshot() -> dict[str, float]:
    """This process's resource usage right now.

    CPU counts children as well as self: ColabFold, ProteinMPNN and Rosetta all
    shell out, so RUSAGE_SELF alone reads near-zero CPU for exactly the stages
    that cost the most. RSS is a kernel high-water mark rather than a counter, so
    it is reported as read instead of differenced.

    Returns:
        Raw readings. `wall` is absolute (the only clock comparable across hosts)
        and `mono` is monotonic (the only clock correct for a delta); both are
        needed and they are not interchangeable.
    """
    me = rusage.getrusage(rusage.RUSAGE_SELF)
    kids = rusage.getrusage(rusage.RUSAGE_CHILDREN)
    return {
        "wall": time.time(),
        "mono": time.perf_counter(),
        "cpu_s": me.ru_utime + me.ru_stime + kids.ru_utime + kids.ru_stime,
        "peak_rss_mb": max(me.ru_maxrss, kids.ru_maxrss) * _RSS_TO_MB,
    }


def _write(
    metrics_dir: str,
    label: str,
    kind: str,
    description: str,
    before: dict[str, float],
    after: dict[str, float],
    error: str | None,
) -> dict[str, Any]:
    """Append one record to this worker process's own file.

    Args:
        metrics_dir: Directory to write into.
        label: Task name (the decorator's `name=`, not the function's).
        kind: Resource label, "cpu" or "gpu".
        description: The task's declared description.
        before: Snapshot taken before the task body ran.
        after: Snapshot taken after it finished or raised.
        error: Formatted exception, or None if the task succeeded.

    Returns:
        The record, whether or not writing it succeeded, so callers can use the
        computed figures without reading the file back.
    """
    row = {
        "task": label,
        "resource": kind,
        "description": description,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "started_at": before["wall"],
        "ended_at": after["wall"],
        "elapsed_s": after["mono"] - before["mono"],
        "cpu_s": after["cpu_s"] - before["cpu_s"],
        "peak_rss_mb": after["peak_rss_mb"],
        "error": error,
    }
    try:
        target = Path(metrics_dir) / f"{row['host']}-{row['pid']}.jsonl"
        with target.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
    except OSError as exc:
        # Deliberately not fatal: losing a metrics line must never be able to
        # take down a twelve-hour cluster run.
        print(f"[metrics] record dropped for {label}: {exc}", flush=True)
    return row


def compute_stats(metrics_dir: str | Path) -> list[dict[str, Any]]:
    """Aggregate every worker's JSONL records into one StageCompute-shaped row
    per (task, resource).

    A plain group-by, so it does not care what order the records were written
    in -- the property that keeps this correct once tasks complete out of order.
    Field names match `demos/cnio/metrics/schema.py`'s StageCompute exactly, so
    `demos/cnio/metrics/collect.py` (which reads a finished run's
    compute_stats.json without knowing who produced it) needs no changes.

    Args:
        metrics_dir: Directory passed to `configure()` for this run.

    Returns:
        Rows sorted by (task, resource). Empty list if the directory holds no
        records yet.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(Path(metrics_dir).glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            groups[(row["task"], row["resource"])].append(row)

    stats = []
    for (name, kind), rows in sorted(groups.items()):
        elapsed = [r["elapsed_s"] for r in rows]
        elapsed_total = sum(elapsed)
        cpu_total = sum(r["cpu_s"] for r in rows)
        stats.append({
            "stage": name,
            "resource": kind,
            "n_tasks": len(rows),
            "elapsed_s_total": elapsed_total,
            "elapsed_s_mean": elapsed_total / len(rows),
            "elapsed_s_max": max(elapsed),
            "cpu_s_total": cpu_total,
            "cpu_per_elapsed": (cpu_total / elapsed_total) if elapsed_total else 0.0,
            "peak_rss_mb_max": max(r["peak_rss_mb"] for r in rows),
            "hosts": sorted({r["host"] for r in rows}),
        })
    return stats
