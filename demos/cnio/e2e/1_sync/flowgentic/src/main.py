"""Track B: AsyncFlow owns the DAG. See DESIGN.md for the architecture and the reasoning
behind each stage's wrapping granularity.

VERIFIED END TO END (jobs 24052, reconfirmed 24054 after this file was simplified, and
again after the 2026-10-19 stages/ modularization -- the restructure changed no logic,
only where it lives, so parity still holds). history.jsonl and qualifying_sequences.csv
match ../original/'s Track A run exactly -- qualifying_sequences.csv byte-identical, every
history.jsonl field identical after normalizing timestamps/hostnames/paths. See results/
in this directory and ../PARITY_REPORT.md for the full comparison, the resource-metrics
caveats (compute_stats.json has two placeholder fields, not measurements; fold's elapsed
time currently absorbs an MSA network wait that Track A bills separately), and -- important
before citing the match as proof of anything beyond "no structural bug" -- why an exact
match was the *expected* outcome here (every stochastic component is seeded identically in
both tracks) and doesn't yet say anything about run-to-run noise.

Structure: this file is only the driver -- it loads config, builds the AsyncFlow engine,
and calls each pipeline stage's `run()` in sequence, threading a `PipelineState` (state.py)
between them. The actual stage logic (dock, geometry, select, design, score_designs,
rosetta, fold, export) lives in stages/, one module per stage. There is deliberately no
local shared module: deployment paths (`cageflow_dir`, `cage_designer_path`) are config
keys, so a task closure captures them as plain strings by value.

Three invariants every stage's task bodies maintain, each learned the hard way:

1. Every task is registered through `flowgentic.metrics.task()` (see
   docs/TELEMETRY.md), which rebuilds the Dragon child's `sys.path` (from
   `flowgentic.metrics.configure()`, called once below) before the task body runs, and
   writes a resource-usage record to disk after it returns. The `import CageFlow` inside
   each task is deliberately redundant with any module-level one -- Dragon child processes
   inherit neither the parent's `sys.path` nor its imported modules. Skipping the
   cage_designer_path root is not a quiet degradation: `select_top_docks` dies with
   "No module named 'src'" (job 24050).

   Why a shared helper is safe HERE but was not when the same function lived in common.py:
   cloudpickle ships anything a task body references from module scope BY REFERENCE, so
   the worker must import it before the task runs. It cannot import a loose file shipped
   beside the job (no sys.path), but it CAN import an installed package, because
   site-packages is always importable. So the rule is not "never share" -- it is "share
   only through something pip-installed in the worker environment." That install is done by
   `pip install --no-deps -e` in scripts/run_sbatch.sh, under `set -euo pipefail`, so a
   failed install aborts the job before this file runs.

2. A task body NEVER references a module OR a shared function that is NOT installed in the
   worker environment -- not even for a single constant. Doing so puts the object in the
   task's closure, which the backend then serializes by reference to a child that cannot
   resolve it. One such reference (`CageFlow.DEFAULT_CROSS_CONTACT_DIST`, for a
   float, inside `task_geometry`) hung every run of this file through job 24049: the task
   submitted, the child launched, and the future never resolved -- confirmed by dumping the
   engine's own state mid-hang (the component was `running`, not stuck in the ready queue)
   and independently by reading cloudpickle's module handling (it serializes a module BY
   REFERENCE, and the Dragon child has no path to re-import it, so deserialization fails
   before the task body ever runs). Constants whose defaults live in CageFlow are resolved
   in the parent and captured by value. `tests/test_no_module_capture.py` enforces this
   with an AST scan across main.py AND every file in stages/.

   Big objects (a materialized dock is ~5.5 MB) are passed as ordinary arguments, not spilled
   to disk -- tested directly: a Dragon task returning and receiving a ~22 MB payload completes
   in under a second.

3. Every history.jsonl write happens in the PARENT process, after a task resolves -- never
   inside a task body that runs in parallel with siblings (design/rosetta/fold). Her leaf
   workers are pure compute with no I/O side effects, so this is enforceable: nothing but each
   stage's own `await`-sequenced code ever calls append_history(). select_top_docks() and
   score_designs() are called directly (zero-dispatch, see DESIGN.md) and do their OWN history
   writing internally, which is safe only because each runs as exactly one task, never
   concurrently with another writer.

KNOWN LIMITATIONS, neither addressed yet:

- Every stage here is awaited before the next is called, so every task registers with the
  engine with zero tracked dependencies -- the DAG is serialized by Python's control flow, not
  resolved by AsyncFlow. Concurrency beyond the `asyncio.gather` calls inside individual stages
  is not real yet. Upstream's own docs: "Awaiting early forces serialization and kills
  concurrency." The fix is `@flow.block` per stage with futures passed as the dependency edges
  (awaiting inside a block does not stall the engine) -- a deliberate restructure, not done here.

- `flowgentic.metrics.task()`'s `resource=` labels each task "cpu" or "gpu" for bookkeeping,
  but that label does NOT reserve or pin anything -- it is a string attached to a metrics
  record, nothing more. AsyncFlow's `task_description={'cpu': N, 'gpu': N}` mechanism exists and
  is forwarded
  correctly as far as `task_backend_specific_kwargs`, but `DragonExecutionBackendV1._submit_task`
  (confirmed by reading it) only ever reads `ranks` out of that dict -- `cpu`/`gpu` are silently
  ignored. The only thing stopping two "gpu"-labeled tasks from actually colliding on the one
  GPU this job holds is that nothing runs concurrently yet (the limitation above). The moment
  fold's per-item tasks run concurrently, this needs a real guard -- e.g. a semaphore sized to
  the job's GPU count around GPU-bound task bodies -- or it will silently oversubscribe.
"""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing as mp
import os
import socket
import sys
import time
from pathlib import Path

import dragon  # noqa: F401  (must be imported before mp.set_start_method("dragon"))

# schema/ is a sibling of src/, not a subdirectory of it, so it isn't on sys.path just
# because this script lives in src/ -- add it explicitly, before any `from state import`
# (here or in stages/*.py, which inherit this process's sys.path once imported below).
FLOWGENTIC_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FLOWGENTIC_DIR / "schema"))

from dotenv import load_dotenv
from radical.asyncflow import WorkflowEngine
from rhapsody.backends import DragonExecutionBackendV1

import flowgentic.discord as fg_discord
import flowgentic.metrics as fg_metrics

from state import PipelineState
from stages import design, dock, export, fold, geometry, rosetta, score_designs, select

CONFIG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else FLOWGENTIC_DIR / "config" / "config.json"

# Where .env lives differs between the local repo (demos/cnio/.env, three parents above
# this file's own flowgentic/ dir) and the cluster deployment (isolation/ collapses
# demos/cnio/e2e into one directory, so the local path's parent count does not apply there).
# FLOWGENTIC_ENV_FILE lets the launcher (run_sbatch.sh) say exactly where it put the file
# instead of this script guessing a relative path that is only correct in one of the two
# places it runs. load_dotenv() is a no-op if the resolved path does not exist, so an unset
# variable and a missing local .env both silently mean "no Discord notifications" -- exactly
# like having never set DISCORD_WEBHOOK_URL at all.
ENV_PATH = Path(os.environ["FLOWGENTIC_ENV_FILE"]) if os.environ.get("FLOWGENTIC_ENV_FILE") \
    else FLOWGENTIC_DIR.parent.parent.parent / ".env"
load_dotenv(ENV_PATH)


def _load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text())
    if not cfg.get("out_dir"):
        cfg["out_dir"] = str(FLOWGENTIC_DIR / "_scratch" / "cageflow_out")
    return cfg


async def main():
    mp.set_start_method("dragon")

    config = _load_config()
    out_dir = Path(config["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    # Her logging.info/.warning calls are real diagnostics; with no handler they'd go to
    # Python's lastResort (WARNING+ only, stderr, no file). Dragon children get the same file
    # via fg_metrics._prepare(), since each is a separate process inheriting none of this.
    log_path = out_dir / "cageflow.log"
    logging.basicConfig(level=logging.INFO, format=fg_metrics.LOG_FORMAT,
                        handlers=[logging.StreamHandler()])
    fg_metrics.attach_log_file(log_path)

    sys.path.insert(0, config["cageflow_dir"])
    # ProteinBuilder's root also needed in the PARENT, not just inside task bodies: some of
    # her parent-side-called helpers (_n_to_materialize -> _prescore_rank_key) internally do
    # `from src.cbeta_interface import ...`, which needs cage_designer_path on sys.path at
    # the point they're CALLED, not just inside the Dragon child processes that ran the leaf
    # workers. Confirmed necessary the hard way: job 24018 crashed here with
    # "ModuleNotFoundError: No module named 'src'" without this line.
    sys.path.insert(0, config["cage_designer_path"])
    import CageFlow

    CageFlow.reset_history(out_dir)

    metrics_dir = out_dir / "metrics"
    fg_metrics.configure(
        metrics_dir,
        sys_paths=(config["cageflow_dir"], config["cage_designer_path"]),
        log_path=log_path,
    )

    job_id = os.environ.get("SLURM_JOB_ID", "local")
    run_start = time.monotonic()

    def elapsed() -> str:
        """mm:ss since run_start -- the same clock every stage ping and the final
        wall_s figure read from, so they stay consistent with each other.
        """
        m, s = divmod(int(time.monotonic() - run_start), 60)
        return f"{m:02d}:{s:02d}"

    fg_discord.run_started(job_id=job_id, node=socket.gethostname(), config=config)

    backend = await DragonExecutionBackendV1()
    flow = await WorkflowEngine.create(backend=backend)

    # Re-assert the log file: Dragon's runtime bring-up calls basicConfig(force=...) while
    # standing up the backend above, which drops handlers it did not install -- so without
    # this, cageflow.log holds only the few lines logged before this point (job 24196).
    fg_metrics.attach_log_file(log_path)

    cage_designer_path = config["cage_designer_path"]
    state = PipelineState()

    # Resolved HERE, in the parent, so no task body closes over the CageFlow module object
    # (see invariant 2 above -- this exact pattern, written inline inside a task, hung
    # every run through job 24049). Needed by both dock (two-component branch) and geometry.
    cross_contact_dist = config.get("cross_contact_dist", CageFlow.DEFAULT_CROSS_CONTACT_DIST)
    cross_clash_dist = config.get("cross_clash_dist", 3.0)

    fg_discord.notify(f"▶️ dock — starting (t+`{elapsed()}`)")
    state = await dock.run(flow, config, cage_designer_path, out_dir, state,
                            cross_contact_dist, cross_clash_dist)

    fg_discord.notify(
        f"▶️ geometry — starting (t+`{elapsed()}`), {len(state.docked_oligomers)} dock(s) docked")
    state = await geometry.run(flow, config, cage_designer_path, out_dir, state,
                                cross_contact_dist, cross_clash_dist)

    fg_discord.notify(f"▶️ select — starting (t+`{elapsed()}`)")
    state = await select.run(flow, config, cage_designer_path, out_dir, state)

    if not state.selected_indices:
        print("Nothing selected -- nothing downstream to run. Writing what we have and exiting.")
        stats = fg_metrics.compute_stats(metrics_dir)
        (out_dir / "compute_stats.json").write_text(json.dumps(stats, indent=2))
        fg_discord.run_finished(
            job_id=job_id, wall_s=time.monotonic() - run_start,
            funnel={"docks": len(state.docked_oligomers), "selected": 0}, out_dir=out_dir,
        )
        await flow.shutdown()
        return

    fg_discord.notify(
        f"▶️ design — starting (t+`{elapsed()}`), {len(state.selected_indices)} dock(s) selected")
    state = await design.run(flow, config, cage_designer_path, out_dir, state)

    fg_discord.notify(f"▶️ score_designs — starting (t+`{elapsed()}`)")
    state = await score_designs.run(flow, config, cage_designer_path, out_dir, state)

    fg_discord.notify(f"▶️ rosetta — starting (t+`{elapsed()}`)")
    state = await rosetta.run(flow, config, cage_designer_path, out_dir, state)

    fg_discord.notify(f"▶️ fold — starting (t+`{elapsed()}`)")
    state = await fold.run(flow, config, cage_designer_path, out_dir, state)

    fg_discord.notify(f"▶️ export_af3 — starting (t+`{elapsed()}`)")
    state = await export.run(flow, config, cage_designer_path, out_dir, state)
    print(f"[export_af3] {len(state.manifest)} job(s) exported")

    # ============================================================================= wrap up ===
    sequence_table = CageFlow.build_sequence_table(out_dir)
    sequence_table.to_csv(out_dir / "qualifying_sequences.csv", index=False)
    CageFlow.style_sequence_table(sequence_table).to_html(out_dir / "qualifying_sequences.html")

    stats = fg_metrics.compute_stats(metrics_dir)
    (out_dir / "compute_stats.json").write_text(json.dumps(stats, indent=2))

    n_folded = sum(1 for per_seq in state.fold_results.values()
                   for r in per_seq.values() if r.get("ok"))
    rosetta_row = next((r for r in stats if r["stage"] == "score_with_rosetta"), None)
    n_rosetta_scored = rosetta_row["n_tasks"] if rosetta_row else 0
    print(f"\nTrack B run complete: {len(state.designed_oligomers)} dock(s) designed, "
          f"{n_rosetta_scored} sequence(s) Rosetta-scored, {n_folded} fold(s) succeeded.")

    fg_discord.run_finished(
        job_id=job_id, wall_s=time.monotonic() - run_start,
        funnel={
            "docks": len(state.docked_oligomers),
            "selected": len(state.selected_indices),
            "designed": sum(len(v) for v in state.designed_oligomers.values()),
            "scored": n_rosetta_scored,
            "folded": n_folded,
            "exported": len(state.manifest),
        },
        out_dir=out_dir,
    )

    await flow.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
