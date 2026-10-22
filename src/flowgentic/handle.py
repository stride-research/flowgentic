"""PipelineHandle: the one contract between her science and his runtime.

Lives in `flowgentic/src/flowgentic/handle.py`. Her stage files import nothing
from flowgentic; they receive a PipelineHandle as their first argument.

Four members:

    handle.config              her science parameters + deployment paths
    handle.out_dir              where this run writes
    handle.run(stage, *deps)    register a stage as a block, return its future
    handle.worker(name=, resource=, description=, **asyncflow_kwargs)
                             decorator; registers an instrumented function_task

WHY EVERY STAGE IS A BLOCK, EVEN IN PHASE 1 WHERE THE WIRING AWAITS EACH ONE
IMMEDIATELY
-----------------------------------------------------------------------------
Two different edges, easy to conflate:

  worker -> worker, inside one stage   a future passed as an argument IS the
                                       edge. AsyncFlow resolves it with no
                                       enclosing block (examples/02-blocks.py's
                                       inner pattern).
  stage  -> stage                      only a registered component (a block)
                                       can be handed out as a future instead
                                       of forcing an await.

So `handle.run` is what lets a stage hand back a receipt rather than a value. In
phase 1 the wiring awaits that receipt immediately, which makes execution
serial -- but the stage is still a real engine component, attributable and
cancellable, and phase 2 becomes "stop awaiting", not "restructure".

POSITIONAL DEPENDENCIES ONLY
-----------------------------
`WorkflowEngine._register_component` detects dependencies by scanning
`comp_desc["args"]` (workflow_manager.py:756) -- there is exactly one call
site, and it does not scan `kwargs`. A future passed by keyword is never
registered as an edge: the component can be submitted before that future
resolves, and `_extract_dependency_values` then calls `.result()` on a pending
future. The Best Practices page says "positional or keyword"; the installed
code disagrees. `handle.run`'s signature makes a keyword future a TypeError at
the call site, so the trap is unreachable here.

NEVER NEST A FUTURE IN A LIST/DICT/TUPLE ARGUMENT
---------------------------------------------------
Only top-level arguments are scanned, so a future inside a container is
silently untracked. Not reachable in phase 1 (stages receive already-resolved
values); worth an AST lint before the phase-2 wiring fans out per candidate.
"""

from __future__ import annotations

import functools
import sys
from pathlib import Path
from typing import Any, Callable


class PipelineHandle:
    """Handed to every stage and every worker it registers.

    Plain data plus two capabilities. Nothing here is specific to any one
    pipeline: `config` and `out_dir` are opaque containers the use case fills,
    and `run`/`worker` only know about AsyncFlow, never about docks or
    sequences or CageFlow.
    """

    def __init__(
        self,
        flow: Any,
        config: dict,
        out_dir: Path,
        *,
        metrics_dir: str | Path,
        sys_paths: tuple[str, ...] = (),
        log_path: str | Path | None = None,
    ) -> None:
        self._flow = flow
        self.config = config
        self.out_dir = Path(out_dir)
        self._metrics_dir = Path(metrics_dir)
        self._sys_paths = tuple(str(p) for p in sys_paths)
        self._log_path = str(log_path) if log_path else None

    # ---- stage-to-stage wiring ------------------------------------------
    def run(self, stage: Callable, *deps: Any) -> Any:
        """Register `stage` as an AsyncFlow block and return its future.

        Args:
            stage: `async def stage(handle, *dependencies)`.
            deps: other stages' futures (or plain values), passed
                POSITIONALLY -- see this module's docstring for why that is
                not optional.

        Returns:
            The block's future. A phase-1 wiring awaits it immediately; a
            phase-2 wiring passes it straight into the next `handle.run` call
            without awaiting, so the engine can overlap independent stages.
        """
        return self._flow.block(stage)(self, *deps)

    # ---- worker registration ----------------------------------------------
    def worker(
        self,
        *,
        name: str,
        resource: str = "cpu",
        description: str = "",
        **asyncflow_kwargs: Any,
    ) -> Callable:
        """Decorator that registers an instrumented AsyncFlow function task.

        This is today's `flowgentic.metrics.task()` decorator, moved here so a
        stage never imports `flowgentic.metrics` itself. Behaviour is
        unchanged: the decorated function must be a NESTED function (defined
        inside the stage that calls this), because a nested function pickles
        BY VALUE regardless of module, while a module-level one in a
        non-installed module pickles by reference and the Dragon child cannot
        import it back to deserialize the task -- the task submits, the child
        dies before any of our code runs, and the future never resolves
        (job 24049). `tests/test_no_module_capture.py` enforces this by AST
        scan, with no cluster needed.

        Args:
            name: Label for this worker's metrics records, independent of the
                Python function name -- e.g. both the one- and two-component
                dock-setup workers record as "dock_setup" even though their
                functions are named differently. Required, not derived from
                `fn.__name__`, so renaming a function never silently renames
                its metrics.
            resource: "cpu" or "gpu". Recorded on every record and used only
                for grouping today -- nothing reserves or pins anything yet.
                Phase 2 enforces it through `task_description`, via
                `**asyncflow_kwargs` below; this parameter does not change.
            description: Free text about what this worker does. Recorded on
                every record, for the phase-3 reasoner to read back.
            **asyncflow_kwargs: Forwarded verbatim to `flow.function_task`, so
                `task_description`, `backend`, `service`, `capture_stdio` keep
                working untouched.

        Returns:
            A decorator. Calling the decorated function returns a real
            AsyncFlow future whose result is the function's own return value,
            unchanged.
        """
        # Bound to locals so the closure below captures plain data by value,
        # never `self` -- `self._flow` is the engine, and a wrapper closing
        # over `self` would try to ship the engine itself into a Dragon child.
        metrics_dir = self._metrics_dir
        sys_paths = self._sys_paths
        log_path = self._log_path
        label, kind, detail = name, resource, description

        def decorate(fn: Callable) -> Callable:
            @self._flow.function_task(**asyncflow_kwargs)
            @functools.wraps(fn)
            async def wrapped(*args: Any, **kwargs: Any) -> Any:
                # Worker side, in the Dragon child. Re-imported from
                # site-packages -- the only import path a fresh process can
                # count on -- rather than inherited from this closure.
                from flowgentic.metrics import _prepare, _snapshot, _write

                _prepare(sys_paths, log_path)
                before = _snapshot()
                try:
                    return await fn(*args, **kwargs)
                finally:
                    exc = sys.exc_info()[1]
                    _write(
                        metrics_dir, label, kind, detail, before, _snapshot(),
                        f"{type(exc).__name__}: {exc}" if exc is not None else None,
                    )

            return wrapped

        return decorate
