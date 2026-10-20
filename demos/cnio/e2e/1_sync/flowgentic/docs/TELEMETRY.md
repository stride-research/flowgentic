# Worker telemetry: design and the question for Matteo

This replaces `start_task`/`compute_row` (from `flowgentic.telemetry`, which lived only on
the cluster copy, never in this repo -- confirmed via `git log --all` and a filesystem
search before building this) with `flowgentic.metrics.task()`, a thin decorator around
`flow.function_task`.

## The one idea

**The worker measures itself and writes the measurement to disk. It is never returned.**

The old pattern routed metrics through the task's return value -- `return dock_index,
result, stat` -- so every consumer of a task's result had to know about and skip over a
measurement it had no use for. Writing to disk instead means a task's return value is
exactly the caller's own data, nothing added. `flowgentic.metrics.task()` wraps
`flow.function_task` and changes nothing else about how AsyncFlow sees the task --
dependency edges, `@flow.block`, `task_description`, cancellation all behave exactly as on
an undecorated task.

```python
@fg.task(flow, name="design_interface", resource="gpu",
         description="ProteinMPNN sequence design for one dock's interface")
async def task_design(dock_index: int, docked_oligomer: dict, geometry: dict):
    from CageFlow import _design_one_interface
    return dock_index, _design_one_interface(...)
```

`name=` is required and deliberately independent of the Python function name: several
task functions share a label (`task_dock_setup` and `task_dock_setup_2c` both record as
`"dock_setup"`, matching the one-component and two-component branches to the same
`compute_stats.json` row the old `compute_row("dock_setup", ...)` calls used). Deriving the
label from `fn.__name__` would have silently renamed rows and broken
`demos/cnio/metrics/collect.py`'s `stage`-keyed reads.

## What gets measured, and why each field survived a cut

Everything comes from `resource.getrusage`, one syscall, no new dependency
(`src/flowgentic` stays stdlib-only, per `pyproject.toml`'s hard constraint -- this package
installs into the cluster's fragile `cageflow_unified_dragon_test` venv). Per task:
`elapsed_s`, `cpu_s` (self **and** children -- ColabFold/ProteinMPNN/Rosetta all shell out,
so self-only CPU reads near zero for exactly the stages that cost the most), `peak_rss_mb`
(a kernel high-water mark, reported as-is rather than differenced), plus `host`/`pid` and
an `error` string if the task raised.

Cut, and kept out deliberately rather than added for completeness: involuntary context
switches, major page faults, I/O block counts, `n_cpus`, anything GPU (no stdlib path
exists, and GPU is not being measured yet). None of these feed a field
`demos/cnio/metrics/schema.py` already defines, and that -- "does this feed a real
downstream consumer" -- is the bar every field here was held to. Easy to add later; none
of them blocking.

## The open question: file count at scale

Metrics land as one JSONL file per worker PROCESS, `{host}-{pid}.jsonl`, so two processes
never write the same file and nothing needs locking. This design assumed Dragon reuses
worker processes across tasks, which would bound file count by the allocation size. **This
is unconfirmed** -- `DESIGN.md` in this same directory states the opposite ("Dragon
launches a real OS process per task"), which would make this one file per TASK instead,
and at 10^5 tasks that is a real parallel-filesystem metadata problem (slow creates,
painful `ls`, the kind of thing that draws admin attention on Lustre/GPFS).

The write rate itself is not the issue -- a few hundred bytes per task, well under one
write/second even at full scale. Only the file count is.

**The fix, if it turns out to matter, changes zero lines of `metrics.py`**: point
`configure(metrics_dir=...)` at node-local disk (`/tmp` under the Slurm job) instead of the
shared output directory, and `cat` each node's files into one per-node file on the shared
filesystem at job end. Deliberately not done pre-emptively -- current runs are O(10) tasks,
this only matters once a run is O(10^4)+, and premature plumbing for it would be exactly
the kind of complexity this design is trying to avoid. **Worth asking Matteo directly**
whether Dragon v1 reuses worker processes, since that one fact decides whether this is
ever needed.

## What is explicitly deferred, not forgotten

- **GPU utilization/memory.** No stdlib path. If it becomes necessary: either read
  `torch.cuda.max_memory_allocated()` opportunistically (only if the task already imported
  torch, so no new dependency) or a separate node-level sampler correlated by timestamp
  afterwards -- a different artifact from per-task metrics, not something to fold into this
  decorator.
- **Queue wait (submit to start).** This is `TaskOverhead` in `schema.py`, and it is the
  actual headline stage-2 number once concurrency is real. The worker cannot know it --
  only the parent, which sees both the submit and start timestamps, can -- so it needs one
  `add_done_callback` in the driver when stage 2 lands. Approximately zero in stage 1
  because nothing queues.
- **Cancellation.** A task cancelled before its body runs (or killed mid-run by the
  backend) writes no record -- the write sits in a `finally` inside the task body, which
  never executes if the body never started. Stage 3's "how many did we kill, what did it
  save" is the number this gap blocks, but nothing in stage 1 calls `.cancel()` on
  anything, so it costs nothing yet. Closing it is a parent-side
  `fut.add_done_callback` that writes an `error="cancelled"` record under the parent's own
  host/pid -- additive, touches none of `metrics.py`/`discord.py`.
- **Resource enforcement (a semaphore around GPU-bound tasks).** `resource="gpu"` on the
  decorator is a recorded label only, exactly like `StageCompute.resource` already was --
  nothing here reserves or pins anything, matching `DragonExecutionBackendV1`, which only
  ever reads `ranks` out of `task_backend_specific_kwargs` (confirmed by reading
  `_submit_task`) and silently drops `cpu`/`gpu`. The moment GPU-bound tasks run
  concurrently (stage 2), nothing stops them oversubscribing the job's one GPU -- see
  `context/open-questions.md` #21. Deliberately not built here: enforcement belongs at the
  `await` site where concurrency actually happens, not inside a decorator that has no
  opinion about scheduling.

## Discord

Kept completely out of the wrapper and out of `metrics.py` -- `flowgentic.discord.notify()`
is a single function the driver calls at its own checkpoints (stage start/end, run
start/end), never once per task. Per-task posting would hit Discord's ~30/min webhook rate
limit within seconds at any real task count; checkpoint posting stays at a handful of
messages per run regardless of how many tasks that run contains.

## Migration note

All 8 stages (`dock`, `geometry`, `select`, `design`, `score_designs`, `rosetta`, `fold`,
`export`) and their 14 task bodies were moved to `flowgentic.metrics.task()` in one pass.
`PipelineState.compute_rows` is gone; `compute_stats.json` is now built entirely from
`flowgentic.metrics.compute_stats(metrics_dir)` at both of `main.py`'s exit points. Verify
against `../original/`'s Track A run before trusting a cluster result: `qualifying_sequences.csv`
byte-identical, `history.jsonl` field-identical after normalizing run-specific noise, and
`compute_stats.json` carrying the same 10 `stage` names with the same `n_tasks` -- but NOT
identical values for `cpu_s_total`/`cpu_per_elapsed`/`peak_rss_mb_max`, which were
placeholders before (`cpu_s_total == elapsed_s_total` exactly, `cpu_per_elapsed == 1.0`,
`peak_rss_mb_max == 0.0` in every row of the old `results/compute_stats.json`) and are real
readings now. That change is the point of this work, not a regression.
