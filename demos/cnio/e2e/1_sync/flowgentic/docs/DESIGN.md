# Track B: AsyncFlow owns the DAG

This is the design for `1_sync/flowgentic/` — the AsyncFlow-driven twin of `1_sync/original/`
(her plain `run_cage_flow()`, now Track A, the fixed point everything here is diffed against).

**Who this is for.** As of 2026-10-16, Laura is maintaining this file going forward, not just us
— she'll keep reimplementing the science *inside* CageFlow's leaf workers, while the step-by-step
glue that calls them now lives here, in our repo. That split only works if the boundary between
"her code" and "this code" is obvious from reading it, not from asking us. This document exists
so a new reader — Laura included — can tell *why* a stage is wrapped the way it is, not just see
that it is.

## The one governing principle

**Every CageFlow pipeline stage is a real AsyncFlow task. The DAG covers the whole pipeline, not
part of it.** A future from one task is passed directly as an argument into the next task's
call — that passing *is* the dependency edge, the same mechanism `t2 = task2(t1)` uses in
AsyncFlow's own hello-world example. No stage runs as a bare Python call outside AsyncFlow's
view. If you're adding a ninth stage later, this is the rule to follow: wrap it as a task, wire
its inputs as the futures it actually depends on, and it's in the graph.

This replaces an earlier, smaller cut where the cheap head of the pipeline (dock, geometry,
select) ran as plain function calls and only design/Rosetta/fold were real AsyncFlow tasks. That
was a reasonable first slice, but it left most of the DAG invisible to the scheduler — exactly
the kind of gap this principle is meant to close for good, not patch over.

## Why we stopped calling her `Executor`/`Budget` — the three-sentence version

Her `Executor.map_timed()` has one operation: submit a batch, block until all of it returns.
That shape cannot express a handle on one piece of work — not because of a missing feature, but
because by the time you have *any* result back, you have *all* of them, and her own
`Executor.cancel()` docstring says a task already running cannot be stopped. AsyncFlow's tasks
are handles from the moment they're created: inspectable, cancellable, chainable individually —
which is the only way stage 2 (move a candidate forward the instant *its* result lands) and
stage 3 (kill a candidate the instant *its* signal looks bad) are expressible at all. We are not
subclassing `Executor` to be AsyncFlow's backend; we're not calling it anywhere.

`Budget` is a different cut, deliberately deferred, not rejected. It exists to make
cost-affordability *decisions* (`affords()`, `scarcest()`) — decisions that only matter once
something is actually choosing between candidates under a cap, which is stage 2/3 work, not this
phase. For now we only need the *numbers* `Budget.compute_report()` used to produce
(elapsed/cpu/memory per stage), not the decisions built on top of them. We get those numbers from
AsyncFlow/Dragon's own `register_callback` instead — see "Metrics" below.

## Per-stage wrapping — per-item vs single-task

Two shapes a stage can take, and the words used for them in the table below:

- **per-item**: the stage becomes N AsyncFlow tasks, one per unit of work (one per sequence, one
  per dock). Each is independently visible to the scheduler.
- **single-task**: the whole stage is one AsyncFlow task that loops over its items internally.
  Visible to the scheduler as one node in the DAG, not N.

The rule: **wherever her own code dispatches many items through `_charge()`/`executor.map_timed()`,
we mirror that granularity — one AsyncFlow task per item, same as she had one `arg_list` entry
per item.** Where her code dispatches nothing (a pure sort, a pure aggregation), there's nothing
to mirror, so the stage is a single task.

Prescore is the one place that rule needs an exception. Her code dispatches it per-pose, but a
pose costs around a millisecond, and Dragon launches a real OS process per task — so copying her
granularity exactly would spend more time starting processes than doing work. We chunk instead:
N tasks sized to the Slurm allocation, which is what her `_chunk_prescore` already does for the
same reason.

| Stage | Her dispatch granularity | Our AsyncFlow granularity | Why |
|---|---|---|---|
| dock — prescore | one item per candidate pose; she already chunks this across `os.cpu_count()` workers (`_chunk_prescore`) | **chunked**: N tasks sized to the Slurm allocation | Measured on a real run: prescore alone took 35.5s over 6120 poses. That's real wall-clock, worth the same parallelism she already built for it — just as Dragon tasks instead of through `Executor`. Not "one task per pose": at ~sub-millisecond-to-millisecond cost per pose, a Dragon process launch per item would cost more than the work itself. |
| dock — materialize | one item per surviving pose (a handful, after prescore narrows the field) | single-task (internal loop) | Measured: ~0.1s/item, a handful of items. Splitting further buys nothing. |
| geometry | one item per dock | single-task (internal loop) | Same reasoning as materialize — cheap, few items. |
| select | none — pure sort/filter over data already in memory | single-task (see note) | The one case where "mirror her dispatch" has nothing to mirror. Kept as its own task purely so `design` has a real future to depend on, not a bare Python value threaded around outside the graph. |
| design | one item per **dock** — ProteinMPNN generates every sequence for a dock in a single subprocess call; there's no finer split without reimplementing ProteinMPNN's own batching | per-item: one task per dock | Already the correct mirror — nothing to adjust. |
| score_designs | none (no `executor`/`budget` parameter in her own signature) | single-task | Same reasoning as select. |
| rosetta | one item per **sequence** (`_score_one_sequence_rosetta` is called once per sequence) | per-item: one task per sequence | This is finer than an earlier draft's per-dock simplification — corrected to match her actual dispatch unit. It's also the stage where per-item cancellation matters most: a bad *sequence*, not a whole dock's worth of sequences, is what stage 3 would want to kill early. |
| fold | one item per sequence, two phases (CPU MSA fetch, GPU inference) | per-item: one task per sequence per phase, chained | Same correction as rosetta. The two phases stay chained (MSA's result feeds inference), matching her own CPU/GPU split for the same reason she built it that way — the MSA fetch is a network-bound CPU wait, inference is the real GPU cost. |
| export_af3 | none — needs the complete rosetta result set to rank candidates | single-task, depends on every rosetta task | Cannot start until everything upstream of it has resolved; the manifest ranks across all candidates at once. |

**The rule in one sentence, for whoever adds a stage 9:** if her code calls `_charge()` over a
list, give AsyncFlow one task per list item, unless the per-item cost is so low that a process
launch would dominate it — in which case chunk, the way dock's prescore step does, rather than
collapsing to a single task by default.

## What a stage's code actually calls

Two categories, and the dividing line is whether her stage function takes an `executor`/`budget`
parameter at all:

- **Zero-dispatch stage functions** (`select_top_docks`, `score_designs`, `export_af3_jobs`) —
  these never took `executor`/`budget` in the first place. Call them directly, unmodified.
- **Executor-coupled stage functions** (`dock_oligomer`, `evaluate_geometry`,
  `design_interface`, `score_with_rosetta`, `fold_oligomer`) — never call these. Call their
  underlying leaf workers instead (`_materialize_dock`, `prescore_solutions`,
  `_evaluate_one_geometry`, `_design_one_interface`, `_score_one_sequence_rosetta`,
  `_fetch_one_msa`, `_fold_one_sequence`), and vendor the pre/post-dispatch glue that used to sit
  around her `_charge()` call ourselves.

Zero lines of `CageFlow.py` are modified for either category — this is a calling-convention
choice, not a code change on her side.

## Metrics: ours, uniformly; Budget's numbers, not its decisions

- **`compute_stats.json`** is built entirely from our own `register_callback` on the Dragon
  backend — per-task start/end/host/elapsed, for every task in the graph, uniformly. This
  replaces `Budget.compute_report()` for every stage now, not just the ones that used to bypass
  it under the earlier design.
- **`budget_summary.json` / `allocation_stats.json` are not written, for now.** `collect.py`
  already handles their absence gracefully (empty list / `None` section) — confirmed in its own
  code, not a gap we're papering over. These come back when stage 2/3 needs a live
  cost-affordability object to consult, not before.
- **`history.jsonl` is still written, in her exact format**, using her own `append_history` /
  `records.dock_outcome` helpers directly. These are pure data functions with no `Executor`
  coupling, so calling them from our own orchestration code after each task resolves costs
  nothing and keeps `collect.py`'s science-metrics parsing working unchanged.

## What CageFlow could drop, and what it definitely can't

Worth stating explicitly rather than letting it be inferred, since this determines what's safe
to delete on the CageFlow side:

**Could be dropped** (nothing here calls it): `executors.py` in its entirety — `Executor`,
`SequentialExecutor`, the pool backends, `DaskSlurmExecutorBackend`, `map`/`map_timed`,
`_timed_call`, `allocation_report`. Also `_charge()`, and `Budget`'s decision surface
(`affords`, `scarcest`, `charge`, `record_stage`, `compute_report`) — we replaced the numbers
with our own `register_callback` timing and deferred the decisions to stage 2/3.

**Definitely still needed** — this file depends on all of it directly: `records.py`
(`dock_outcome`, `group_score`, `interface_residue_sets`, `flat_interface_resnums`,
`is_two_component`, `ZERO_DESIGN_SUMMARY`), `history_io.py` (`append_history`, `reset_history`),
`history_table.py` (`build_sequence_table`, `style_sequence_table`), every leaf worker listed
above, plus `select_top_docks` / `score_designs` / `export_af3_jobs` called whole, and the
helper layer they rest on (`postredesign_selection`, `symmdef`, `helpers/`, `rosetta_scoring`,
`af3_export`, `native_gaps`). The `Executor` is gone; the science library very much is not.

## What this is diffed against

`1_sync/original/` (Track A, her plain `run_cage_flow()`) is the fixed point. The acceptance test
for this file is not "does it run" — it's that the science in its output (`history.jsonl`,
`qualifying_sequences.csv`) matches Track A's, modulo whatever non-determinism a repeat Track A
run already shows. Divergence beyond that is ours to explain.
