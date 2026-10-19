"""Track B: AsyncFlow owns the DAG. See DESIGN.md for the architecture and the reasoning
behind each stage's wrapping granularity.

VERIFIED END TO END (jobs 24052, reconfirmed 24054 after this file was simplified).
history.jsonl and qualifying_sequences.csv match ../original/'s Track A run exactly --
qualifying_sequences.csv byte-identical, every history.jsonl field identical after
normalizing timestamps/hostnames/paths. See results/ in this directory and
../PARITY_REPORT.md for the full comparison, the resource-metrics caveats (compute_stats.json
has two placeholder fields, not measurements; fold's elapsed time currently absorbs an MSA
network wait that Track A bills separately), and -- important before citing the match as proof
of anything beyond "no structural bug" -- why an exact match was the *expected* outcome here
(every stochastic component is seeded identically in both tracks) and doesn't yet say anything
about run-to-run noise.

Three invariants this file maintains, each learned the hard way:

1. Every task body calls `_task_setup(cage_designer_path)` first, and re-does its own imports.
   Dragon child processes inherit neither the parent's `sys.path` nor its imported modules, so
   the `import CageFlow` inside each task is deliberately redundant with the module-level one.
   Skipping the `cage_designer_path` half of this is not a quiet degradation: `select_top_docks`
   dies with "No module named 'src'" (job 24050).

2. A task body NEVER references a module imported in the parent -- not even for a single
   constant. Doing so puts the whole module object in the task's closure, which the backend
   then has to serialize to the child. One such reference
   (`CageFlow.DEFAULT_CROSS_CONTACT_DIST`, for a float, inside `task_geometry`) hung every
   run of this file through job 24049: the task submitted, the child launched, and the future
   never resolved -- confirmed by dumping the engine's own state mid-hang (the component was
   `running`, not stuck in the ready queue) and independently by reading cloudpickle's module
   handling (it serializes a module BY REFERENCE, and the Dragon child has no path to
   re-import it, so deserialization fails before the task body ever runs). Constants whose
   defaults live in CageFlow are resolved in the parent and captured by value.
   `tests/test_no_module_capture.py` enforces this with an AST scan.

   Big objects (a materialized dock is ~5.5 MB) are passed as ordinary arguments, not spilled
   to disk -- tested directly: a Dragon task returning and receiving a ~22 MB payload completes
   in under a second. An earlier version of this file spilled every multi-MB value to
   `out_dir/_spill/` on the theory that Dragon's result queue would deadlock on a large payload;
   that theory was never actually what caused a hang (invariant 2's module reference was), and
   removing it cost nothing. Revisit only with a measured reason, not a guess, if payload size
   ever becomes a real config (e.g. whole-cage folding).

3. Every history.jsonl write happens in the PARENT process, after a task resolves -- never
   inside a task body that runs in parallel with siblings (design/rosetta/fold). Her leaf
   workers are pure compute with no I/O side effects, so this is enforceable: nothing but this
   file's own `await`-sequenced code ever calls append_history(). select_top_docks() and
   score_designs() are called directly (zero-dispatch, see DESIGN.md) and do their OWN history
   writing internally, which is safe only because each runs as exactly one task, never
   concurrently with another writer.

KNOWN LIMITATIONS, neither addressed yet:

- Every stage here is awaited before the next is defined, so every task registers with the
  engine with zero tracked dependencies -- the DAG is serialized by Python's control flow, not
  resolved by AsyncFlow. Concurrency beyond the one `asyncio.gather` in prescore is not real
  yet. Upstream's own docs: "Awaiting early forces serialization and kills concurrency." The
  fix is `@flow.block` per stage with futures passed as the dependency edges (awaiting inside a
  block does not stall the engine) -- a deliberate restructure, not done here.

- `compute_rows`/`_compute_row` labels each stage "cpu" or "gpu" for bookkeeping, but that
  label does NOT reserve or pin anything -- it is a string we attach to a metrics row, nothing
  more. AsyncFlow's `task_description={'cpu': N, 'gpu': N}` mechanism exists and is forwarded
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
import math
import multiprocessing as mp
import os
import socket
import sys
import time
from pathlib import Path

import dragon  # noqa: F401  (must be imported before mp.set_start_method("dragon"))

from radical.asyncflow import WorkflowEngine
from rhapsody.backends import DragonExecutionBackendV1

CAGEFLOW_DIR = Path.home() / "software" / "CageFlow"
CONFIG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "config.json"

#: How many prescore chunks to split a dock run's pose grid into -- mirrors her own
#: _chunk_prescore's chunking (see DESIGN.md's dock/prescore row), run as AsyncFlow tasks
#: instead of through _charge(). Read from the Slurm allocation rather than os.cpu_count():
#: these nodes have 512 logical CPUs but our sbatch asks for 8, and os.cpu_count() reports the
#: former -- which is exactly the mismatch that drained three of Laura's nodes when ProteinMPNN
#: sized its own pool that way (notes/handover_2026-10-12.md). Falls back to 8 only when run
#: outside a Slurm allocation.
N_PRESCORE_CHUNKS = int(os.environ.get("SLURM_CPUS_PER_TASK") or 8)


def _load_config() -> dict:
    cfg = json.loads(CONFIG_PATH.read_text())
    if not cfg.get("out_dir"):
        cfg["out_dir"] = str(Path(__file__).resolve().parent / "_scratch" / "cageflow_out")
    return cfg


def _task_setup(cage_designer_path: str) -> None:
    """Prepare a Dragon child process to run a CageFlow leaf worker. Call FIRST in every
    task body, before any other import.

    A child inherits neither the parent's `sys.path` nor its imported modules, so each task
    rebuilds both roots itself:

    * `CAGEFLOW_DIR` -- CageFlow and its sibling modules (`records`, `native_gaps`, ...).
    * `cage_designer_path` -- ProteinBuilder, which CageFlow's helpers import as `src.*`.
      Omitting this is not a quiet degradation: `select_top_docks` dies with
      "No module named 'src'" (job 24050).

    """
    import sys as _sys
    import os as _os

    for root in (str(CAGEFLOW_DIR), _os.path.abspath(cage_designer_path)):
        if root not in _sys.path:
            _sys.path.insert(0, root)


def _compute_row(stage: str, resource: str, stats: list[dict]) -> dict:
    """One StageCompute-shaped row (see metrics/schema.py), built from our own per-task timing.

    Deliberately not sourced from Budget.compute_report() -- see DESIGN.md's Metrics section.
    `stats` is a list of {"elapsed_s", "host"} dicts, one per task in this stage.
    """
    elapsed = [s["elapsed_s"] for s in stats]
    hosts = sorted(set(s["host"] for s in stats))
    return {
        "stage": stage, "resource": resource, "n_tasks": len(stats),
        "elapsed_s_total": sum(elapsed), "elapsed_s_mean": sum(elapsed) / len(elapsed),
        "elapsed_s_max": max(elapsed),
        # cpu_s/peak_rss are not measured here -- that needs psutil/resource sampling inside
        # each task body, which we haven't added yet. cpu_per_elapsed=1.0 is a placeholder, not
        # a measurement; fix this before trusting compute_stats.json's CPU-efficiency numbers.
        "cpu_s_total": sum(elapsed), "cpu_per_elapsed": 1.0,
        "peak_rss_mb_max": 0.0, "hosts": hosts,
    }


async def main():
    mp.set_start_method("dragon")

    config = _load_config()
    out_dir = Path(config["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(CAGEFLOW_DIR))
    # ProteinBuilder's root also needed in the PARENT, not just inside task bodies: some of
    # her parent-side-called helpers (_n_to_materialize -> _prescore_rank_key) internally do
    # `from src.cbeta_interface import ...`, which needs cage_designer_path on sys.path at
    # the point they're CALLED, not just inside the Dragon child processes that ran the leaf
    # workers. Confirmed necessary the hard way: job 24018 crashed here with
    # "ModuleNotFoundError: No module named 'src'" without this line.
    sys.path.insert(0, config["cage_designer_path"])
    import CageFlow
    import records

    CageFlow.reset_history(out_dir)

    backend = await DragonExecutionBackendV1()
    flow = await WorkflowEngine.create(backend=backend)

    compute_rows: list[dict] = []
    cage_designer_path = config["cage_designer_path"]
    mpnn_dir = config.get("mpnn_dir", "ProteinMPNN")

    # Resolved HERE, in the parent, so no task body closes over the CageFlow module object
    # (see invariant 2 above -- this exact pattern, written inline inside a task, hung
    # every run through job 24049). Moved above the dock section, rather than left at
    # geometry where it used to live, because dock_two_component's own setup needs both
    # values too.
    cross_contact_dist = config.get("cross_contact_dist", CageFlow.DEFAULT_CROSS_CONTACT_DIST)
    cross_clash_dist = config.get("cross_clash_dist", 3.0)

    if not config.get("scaffold_pdb_b"):
        # ================================================================== dock: setup + scan ===
        @flow.function_task
        async def task_dock_setup():
            _t0 = time.monotonic()
            _task_setup(cage_designer_path)
            import CageFlow as _cf
            from pathlib import Path as _Path

            from src.symmetry import (
                get_symmetry_matrices, get_canonical_axis, get_coset_reps, normalize_symmetry)
            from src.geometry import align_axes
            from src.pdb_utils import (
                get_center_of_mass, get_oligomer_axis, get_chains_from_pdb,
                harmonize_chain_residue_ranges)
            from src.docking import scan_docking

            pdb_raw = _Path(config["scaffold_pdb"]).read_text()
            pdb_raw, dropped, skipped = harmonize_chain_residue_ranges(pdb_raw)
            if dropped:
                print(f"  NOTE: harmonized chain residue ranges before docking: {dropped}")
            if skipped:
                print(f"  WARNING: grouped chains with incompatible residue numbering, left "
                      f"unharmonized: {skipped}. Chains stay unequal, which breaks ProteinMPNN "
                      f"position tying and skews axis/COM estimation.")

            symmetry = normalize_symmetry(config["symmetry"])
            n_chains = len(get_chains_from_pdb(pdb_raw))

            pdb_raw, prep_info = _cf._prepare_component(
                pdb_raw, n_chains, "(component)", cage_designer_path, config)
            _cf._write_preparation_report(config.get("out_dir"), {"component": prep_info})

            sym_matrices = get_symmetry_matrices(symmetry)
            canonical_axis = get_canonical_axis(sym_matrices, n_chains, symmetry)
            coset_reps = get_coset_reps(sym_matrices, canonical_axis)

            com = get_center_of_mass(pdb_raw)
            oligo_axis = get_oligomer_axis(pdb_raw)
            R_align = align_axes(oligo_axis, canonical_axis)

            dr = config["dock_ranges"]
            is_high_sym = symmetry in ("T", "O", "I")
            flip = config.get("flip", False)

            radius_min, radius_max = _cf._resolve_radius_window(
                pdb_raw, coset_reps, canonical_axis, R_align, is_high_sym, flip,
                cage_designer_path, config)
            spin_period = _cf._spin_period(pdb_raw, n_chains, "(component)", cage_designer_path, config)

            # grid_size, computed exactly as she does -- it is passed as scan_docking's top_n,
            # which is how "return EVERY candidate in the grid, no truncation" is expressed. An
            # earlier draft substituted an arbitrary large constant here; same intent, but this
            # is the number her code actually passes.
            import math as _math
            spin_step = dr.get("spin_step", 5.0)
            n_spins = _math.ceil(spin_period / spin_step)
            n_radii = int((radius_max - radius_min) / dr.get("radius_step", 1.0)) + 1
            n_lats = (int(180 / dr.get("lat_step", 15.0)) + 1) if not is_high_sym else 1
            n_lons = int(360 / dr.get("lon_step", 15.0)) if not is_high_sym else 1
            grid_size = n_spins * n_radii * n_lats * n_lons

            solutions = scan_docking(
                pdb_raw, coset_reps, canonical_axis, R_align,
                spin_step=dr.get("spin_step", 5.0), lat_step=dr.get("lat_step", 15.0),
                lon_step=dr.get("lon_step", 15.0), radius_min=radius_min, radius_max=radius_max,
                radius_step=dr.get("radius_step", 1.0),
                clash_dist=config.get("clash_dist", 3.0), contact_dist=config.get("contact_dist", 8.0),
                min_contacts=0, filter_clashes=False,
                w_quality=config.get("w_quality", 1.0), w_clash=config.get("w_clash", 1.0),
                w_radius=config.get("w_radius", 0.1), w_helix=config.get("w_helix", 0.0),
                min_helix_contacts=config.get("min_helix_contacts", 3),
                scan_lat=not is_high_sym, scan_lon=not is_high_sym, flip=flip,
                top_n=grid_size, spin_period=spin_period,
            )
            if not solutions:
                raise RuntimeError("scan_docking found no feasible docking solution in dock_ranges.")

            stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
            return (pdb_raw, canonical_axis, coset_reps, R_align, com, is_high_sym, solutions), stat

        (pdb_raw, canonical_axis, coset_reps, R_align, com, is_high_sym,
         solutions), setup_stat = await task_dock_setup()
        compute_rows.append(_compute_row("dock_setup", "cpu", [setup_stat]))
        print(f"[dock] scan_docking: {len(solutions)} candidate poses")

        # ===================================================================== dock: prescore ===
        max_mat = config.get("max_materialized")
        if max_mat is not None and len(solutions) > min(CageFlow.MATERIALIZE_FLOOR, int(max_mat)):
            chunk_size = math.ceil(len(solutions) / N_PRESCORE_CHUNKS)
            chunks = [solutions[i:i + chunk_size] for i in range(0, len(solutions), chunk_size)]

            @flow.function_task
            async def task_prescore_chunk(chunk):
                _t0 = time.monotonic()
                _task_setup(cage_designer_path)
                from CageFlow import prescore_solutions
                result = prescore_solutions(
                    pdb_raw, canonical_axis, coset_reps, R_align, com, chunk, config,
                    cage_designer_path, flip=config.get("flip", False))
                stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
                return result, stat

            _t0 = time.monotonic()
            chunk_tasks = [task_prescore_chunk(c) for c in chunks]
            chunk_results = await asyncio.gather(*chunk_tasks)
            prescores = []
            chunk_stats = []
            for part, stat in chunk_results:
                prescores.extend(part)
                chunk_stats.append(stat)
            compute_rows.append(_compute_row("prescore", "cpu", chunk_stats))

            n_keep = CageFlow._n_to_materialize(prescores, config)
            order = sorted(range(len(solutions)), key=CageFlow._prescore_rank_key(prescores, config))
            keep = sorted(order[:n_keep])
            n_ok = sum(1 for p in prescores if p["steric_ok"])
            print(f"[dock] prescore: {len(solutions)} poses in {time.monotonic() - _t0:.1f}s "
                  f"({n_ok} sterically admissible); materializing {len(keep)}")
            solutions = [solutions[i] for i in keep]

        # ==================================================================== dock: materialize ===
        @flow.function_task
        async def task_materialize():
            _t0 = time.monotonic()
            _task_setup(cage_designer_path)
            from CageFlow import _materialize_dock
            flip = config.get("flip", False)
            docked = [
                _materialize_dock(pdb_raw, canonical_axis, coset_reps, R_align, com, is_high_sym,
                                   sol, cage_designer_path, flip)
                for sol in solutions
            ]
            stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
            return docked, stat

        docked_oligomers, materialize_stat = await task_materialize()
        compute_rows.append(_compute_row("dock_oligomer", "cpu", [materialize_stat]))
        print(f"[dock] materialized {len(docked_oligomers)} dock(s)")
    else:
        # ======================================================== dock: two-component (2c) ===
        # Faithful port of dock_two_component() (CageFlow.py:623-904). Downstream code
        # (geometry's cross_* branch, Rosetta's _2c scorer, fold's two-component skip)
        # already existed and was unreachable dead code until this branch; nothing past
        # this point needed to change to support it.
        @flow.function_task
        async def task_dock_setup_2c():
            _t0 = time.monotonic()
            _task_setup(cage_designer_path)
            import CageFlow as _cf
            from pathlib import Path as _Path
            import math as _math

            from src.architectures import ARCHITECTURES_BY_KEY
            from src.symmetry import get_symmetry_matrices, get_canonical_axis, get_coset_reps
            from src.geometry import align_axes
            from src.pdb_utils import (
                get_center_of_mass, get_oligomer_axis, get_chains_from_pdb,
                harmonize_chain_residue_ranges)
            from src.docking import _scan_2c_prepare

            arch_key = config["architecture"]
            arch = ARCHITECTURES_BY_KEY.get(arch_key)
            if arch is None:
                raise RuntimeError(f"Unknown architecture {arch_key!r}. "
                                   f"Known: {sorted(ARCHITECTURES_BY_KEY)}")
            if arch.n_components != 2:
                raise RuntimeError(
                    f"architecture {arch_key!r} is a one-component cage; use scaffold_pdb "
                    f"alone (without scaffold_pdb_b) for that.")

            pdb_raw_a = _Path(config["scaffold_pdb"]).read_text()
            pdb_raw_b = _Path(config["scaffold_pdb_b"]).read_text()
            for label, raw in (("A", pdb_raw_a), ("B", pdb_raw_b)):
                harmonized, dropped, skipped = harmonize_chain_residue_ranges(raw)
                if dropped:
                    print(f"  NOTE: harmonized component {label} chain residue ranges: {dropped}")
                if skipped:
                    print(f"  WARNING: component {label} has grouped chains with incompatible "
                          f"residue numbering, left unharmonized: {skipped}. Chains stay unequal, "
                          f"which breaks ProteinMPNN position tying and skews axis/COM estimation.")
                if label == "A":
                    pdb_raw_a = harmonized
                else:
                    pdb_raw_b = harmonized

            n_chains_a = len(get_chains_from_pdb(pdb_raw_a))
            n_chains_b = len(get_chains_from_pdb(pdb_raw_b))
            if n_chains_a != arch.order_a or n_chains_b != arch.order_b:
                raise RuntimeError(
                    f"architecture {arch_key!r} expects a C{arch.order_a} component A and a "
                    f"C{arch.order_b} component B, but the loaded structures have "
                    f"{n_chains_a} and {n_chains_b} chains. Check that each scaffold PDB is "
                    f"the biological assembly (the oligomer), not a single protomer.")

            pdb_raw_a, prep_info_a = _cf._prepare_component(
                pdb_raw_a, n_chains_a, "A", cage_designer_path, config)
            pdb_raw_b, prep_info_b = _cf._prepare_component(
                pdb_raw_b, n_chains_b, "B", cage_designer_path, config)
            _cf._write_preparation_report(
                config.get("out_dir"), {"component_a": prep_info_a, "component_b": prep_info_b})

            sym_matrices = get_symmetry_matrices(arch.point_group)
            # NOTE: get_canonical_axis's two-component call does NOT pass a sym_name
            # positional argument the way the one-component path above does -- its
            # third positional slot is `exclude_axis`, and the real call never fills
            # `sym_name` at all. Passing the point group there by analogy with the
            # one-component branch would silently miscall this.
            axis_a = get_canonical_axis(sym_matrices, n_chains_a)
            if arch.same_axis:
                axis_b = axis_a
            elif arch.invert_axis:
                axis_b = -axis_a
            else:
                axis_b = get_canonical_axis(sym_matrices, n_chains_b, exclude_axis=axis_a)
            reps_a = get_coset_reps(sym_matrices, axis_a)
            reps_b = reps_a if arch.same_axis else get_coset_reps(sym_matrices, axis_b)

            com_a = get_center_of_mass(pdb_raw_a)
            com_b = get_center_of_mass(pdb_raw_b)
            R_align_a = align_axes(get_oligomer_axis(pdb_raw_a), axis_a)
            R_align_b = align_axes(get_oligomer_axis(pdb_raw_b), axis_b)

            dr = config["dock_ranges"]
            spin_step_a = dr.get("spin_step_a", dr.get("spin_step", 10.0))
            spin_step_b = dr.get("spin_step_b", dr.get("spin_step", 10.0))
            r_step_a = dr.get("radius_step_a", dr.get("radius_step", 2.0))
            r_step_b = dr.get("radius_step_b", dr.get("radius_step", 2.0))

            def _radius_window(component):
                if f"radius_{component}" in dr:
                    return dr[f"radius_{component}"]
                if "radius" in dr:
                    return dr["radius"]
                raise RuntimeError(
                    f"dock_ranges needs a radius window for component {component.upper()}: "
                    f"set 'radius_{component}' (preferred for two-component cages) or a "
                    f"shared 'radius' for both.")

            r_a = _radius_window("a")
            r_b = _radius_window("b")
            flip_a = config.get("flip_a", False)
            flip_b = config.get("flip_b", False)

            spin_period_a = _cf._spin_period(pdb_raw_a, n_chains_a, "A", cage_designer_path, config)
            spin_period_b = _cf._spin_period(pdb_raw_b, n_chains_b, "B", cage_designer_path, config)
            n_spin_a = _math.ceil(spin_period_a / spin_step_a)
            n_spin_b = _math.ceil(spin_period_b / spin_step_b)
            grid_size = n_spin_a * n_spin_b

            # Only what _scan_2c_prepare (STAGE 1, below) needs. min_cross_contacts and
            # w_cross_* are STAGE 2-only (_scan_2c_chunk_worker) and are read fresh inside
            # task_scan_2c_chunk instead -- defining them here too would be dead code.
            min_self_contacts = config.get("min_self_contacts_2c", 0)
            w_quality = config.get("w_quality", 1.0)
            w_clash = config.get("w_clash", 1.0)
            w_radius = config.get("w_radius", 0.1)
            clash_dist = config.get("clash_dist", 3.0)
            contact_dist = config.get("contact_dist", 8.0)

            # STAGE 1, inline -- "comparatively cheap" per her own comment, and everything
            # it returns has to ship to Stage 2's workers anyway, so there is no benefit to
            # a separate task boundary here.
            pre_a, pre_b, interaction_cutoff = _scan_2c_prepare(
                pdb_raw_a, reps_a, axis_a,
                pdb_raw_b, reps_b, axis_b,
                R_align_a, R_align_b,
                spin_step_a, spin_step_b,
                r_a[0], r_a[1], r_step_a,
                r_b[0], r_b[1], r_step_b,
                clash_dist, contact_dist, min_self_contacts,
                cross_clash_dist, cross_contact_dist,
                w_quality, w_clash, w_radius,
                flip_a=flip_a, flip_b=flip_b,
                spin_period_a=spin_period_a, spin_period_b=spin_period_b,
                joint_radius_grid=config.get("joint_radius_grid", 10),
                joint_radius_window=config.get("joint_radius_window", 20.0),
            )
            if not pre_a or not pre_b:
                raise RuntimeError(
                    f"the two-component scan found no valid self-placement for component "
                    f"{'A' if not pre_a else 'B'} anywhere in its radius window, so there is "
                    f"nothing to pair. Try widening dock_ranges.radius_"
                    f"{'a' if not pre_a else 'b'}, raising contact_dist, or lowering "
                    f"min_self_contacts_2c.")

            # The coset reduction is claimed only when idealisation ACTUALLY ran on both
            # components -- see her comment at CageFlow.py:778-782 for why.
            ref_coset_only = all(
                info.get("symmetrized") and info.get("ok") and not info.get("skipped")
                for info in (prep_info_a, prep_info_b))

            stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
            return (pdb_raw_a, pdb_raw_b, axis_a, axis_b, reps_a, reps_b, R_align_a, R_align_b,
                    com_a, com_b, pre_a, pre_b, interaction_cutoff, ref_coset_only, grid_size,
                    flip_a, flip_b), stat

        (pdb_raw_a, pdb_raw_b, axis_a, axis_b, reps_a, reps_b, R_align_a, R_align_b,
         com_a, com_b, pre_a, pre_b, interaction_cutoff, ref_coset_only, grid_size,
         flip_a, flip_b), setup_stat = await task_dock_setup_2c()
        compute_rows.append(_compute_row("dock_setup", "cpu", [setup_stat]))

        # STAGE 2: pair search, fanned across chunks of candidate A -- the dominant cost
        # of two-component docking, per her own comment. config["max_workers"] is read
        # first, exactly as her code reads it; the fallback is N_PRESCORE_CHUNKS (the
        # Slurm-allocation-sized count), NOT os.cpu_count(), for the identical
        # oversubscription reason N_PRESCORE_CHUNKS itself exists (see its own comment
        # above) -- her fallback to os.cpu_count() is the same mismatch in a second place,
        # not a second thing to faithfully copy.
        min_cross_contacts = config.get("min_cross_contacts", 1)
        n_chunks = max(1, min(config.get("max_workers") or N_PRESCORE_CHUNKS, len(pre_a)))
        chunk_size = -(-len(pre_a) // n_chunks)
        chunks_a = [pre_a[i:i + chunk_size] for i in range(0, len(pre_a), chunk_size)]
        print(f"[dock_2c] pair search: {len(pre_a)} x {len(pre_b)} = "
              f"{len(pre_a) * len(pre_b)} candidate pairs over {len(chunks_a)} chunk(s)")

        @flow.function_task
        async def task_scan_2c_chunk(chunk_a: list, grid_size: int):
            _t0 = time.monotonic()
            _task_setup(cage_designer_path)
            from CageFlow import _scan_2c_chunk_worker
            result = _scan_2c_chunk_worker(
                chunk_a, pre_b, interaction_cutoff, cross_clash_dist, cross_contact_dist,
                min_cross_contacts, config.get("w_quality", 1.0), config.get("w_clash", 1.0),
                config.get("w_radius", 0.1), config.get("w_cross_quality", 1.5),
                config.get("w_cross_clash", 1.0), grid_size, cage_designer_path, ref_coset_only)
            stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
            return result, stat

        _t0 = time.monotonic()
        scan_tasks = [task_scan_2c_chunk(c, grid_size) for c in chunks_a]
        scan_results = await asyncio.gather(*scan_tasks)
        solutions = []
        scan_stats = []
        for part, stat in scan_results:
            solutions.extend(part)
            scan_stats.append(stat)
        compute_rows.append(_compute_row("dock_two_component_pair_search", "cpu", scan_stats))

        # Global merge: sort by score, truncate to grid_size -- exactly as her code does
        # (CageFlow.py:802-803). Per-chunk top_n already guarantees nothing in the global
        # top N was dropped at the chunk stage.
        solutions.sort(key=lambda x: x["score"], reverse=True)
        solutions = solutions[:grid_size]
        if not solutions:
            raise RuntimeError(
                "the two-component pair search found no feasible solution. Try widening "
                "dock_ranges.radius, raising cross_contact_dist, or lowering "
                "min_cross_contacts.")
        print(f"[dock_2c] pair search: {len(solutions)} solution(s) in "
              f"{time.monotonic() - _t0:.1f}s")

        # Optional prescore, mirroring the one-component block above -- same chunking
        # pattern, her two-component prescore worker instead of the one-component one.
        max_mat = config.get("max_materialized")
        if max_mat is not None and len(solutions) > min(CageFlow.MATERIALIZE_FLOOR, int(max_mat)):
            chunk_size = math.ceil(len(solutions) / N_PRESCORE_CHUNKS)
            chunks = [solutions[i:i + chunk_size] for i in range(0, len(solutions), chunk_size)]

            @flow.function_task
            async def task_prescore_2c_chunk(chunk):
                _t0 = time.monotonic()
                _task_setup(cage_designer_path)
                from CageFlow import prescore_solutions_2c
                result = prescore_solutions_2c(
                    pdb_raw_a, pdb_raw_b, axis_a, axis_b, reps_a, reps_b, R_align_a, R_align_b,
                    com_a, com_b, chunk, config, cage_designer_path, flip_a=flip_a, flip_b=flip_b)
                stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
                return result, stat

            _t0 = time.monotonic()
            chunk_tasks = [task_prescore_2c_chunk(c) for c in chunks]
            chunk_results = await asyncio.gather(*chunk_tasks)
            prescores = []
            chunk_stats = []
            for part, stat in chunk_results:
                prescores.extend(part)
                chunk_stats.append(stat)
            compute_rows.append(_compute_row("prescore", "cpu", chunk_stats))

            n_keep = CageFlow._n_to_materialize(prescores, config)
            order = sorted(range(len(solutions)), key=CageFlow._prescore_rank_key(prescores, config))
            keep = sorted(order[:n_keep])
            n_ok = sum(1 for p in prescores if p["steric_ok"])
            print(f"[dock_2c] prescore: {len(solutions)} poses in {time.monotonic() - _t0:.1f}s "
                  f"({n_ok} sterically admissible); materializing {len(keep)}")
            solutions = [solutions[i] for i in keep]

        @flow.function_task
        async def task_materialize_2c():
            _t0 = time.monotonic()
            _task_setup(cage_designer_path)
            from CageFlow import _materialize_dock_2c
            docked = [
                _materialize_dock_2c(pdb_raw_a, pdb_raw_b, axis_a, axis_b, reps_a, reps_b,
                                     R_align_a, R_align_b, com_a, com_b, sol, cage_designer_path,
                                     flip_a=flip_a, flip_b=flip_b)
                for sol in solutions
            ]
            stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
            return docked, stat

        docked_oligomers, materialize_stat = await task_materialize_2c()
        compute_rows.append(_compute_row("dock_oligomer", "cpu", [materialize_stat]))
        print(f"[dock_2c] materialized {len(docked_oligomers)} two-component dock(s)")

    # ========================================================================== geometry ===
    # cross_contact_dist/cross_clash_dist are already resolved above (see invariant 2),
    # before the dock section -- dock_two_component's own setup needs them too.
    @flow.function_task
    async def task_geometry(docked_oligomers: list[dict]):
        _t0 = time.monotonic()
        _task_setup(cage_designer_path)
        from CageFlow import _evaluate_one_geometry
        from records import is_two_component

        clash_dist = config.get("clash_dist", 3.0)
        contact_dist = config.get("contact_dist", 8.0)
        min_contacts = config.get("min_contacts", 3)
        cbeta_noise = config.get("cbeta_noise", 0.5)
        cbeta_min_cb10 = config.get("cbeta_min_cb10", 100)
        clash_carbon_dist = config.get("clash_carbon_dist", 3.0)
        clash_floor_dist = config.get("clash_floor_dist", 2.5)
        clash_atoms = config.get("clash_atoms", "bb_cb")
        steric_gate = config.get("steric_gate", False)
        two_component = is_two_component(docked_oligomers)

        if two_component:
            arg_list = [
                ([d["copies_a"], d["copies_b"]], cross_clash_dist, cross_contact_dist,
                 min_contacts, cage_designer_path, cbeta_noise, cbeta_min_cb10,
                 clash_carbon_dist, clash_floor_dist, steric_gate, clash_atoms)
                for d in docked_oligomers
            ]
        else:
            arg_list = [
                ([d["copies"]], clash_dist, contact_dist, min_contacts, cage_designer_path,
                 cbeta_noise, cbeta_min_cb10, clash_carbon_dist, clash_floor_dist,
                 steric_gate, clash_atoms)
                for d in docked_oligomers
            ]
        geoms = [_evaluate_one_geometry(*args) for args in arg_list]
        stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
        return geoms, stat

    if not docked_oligomers:
        raise RuntimeError("dock must run before evaluate_geometry().")

    geometries, geometry_stat = await task_geometry(docked_oligomers)
    compute_rows.append(_compute_row("evaluate_geometry", "cpu", [geometry_stat]))

    n_passed = 0
    for i, g in enumerate(geometries):
        if g["passed"]:
            n_passed += 1
        else:
            CageFlow.append_history(
                out_dir, records.dock_outcome(i, docked_oligomers[i], g, status="geometry_failed"))
    print(f"[geometry] {n_passed}/{len(geometries)} passed")

    # ============================================================================= select ===
    @flow.function_task
    async def task_select(docked_oligomers: list[dict], geometries: list[dict]):
        _t0 = time.monotonic()
        _task_setup(cage_designer_path)
        import CageFlow as _cf
        sel = _cf.select_top_docks(docked_oligomers, geometries, config, str(out_dir))
        stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
        return sel, stat

    selected_indices, select_stat = await task_select(docked_oligomers, geometries)
    compute_rows.append(_compute_row("select_top_docks", "cpu", [select_stat]))
    print(f"[select] selected {selected_indices}")

    if not selected_indices:
        print("Nothing selected -- nothing downstream to run. Writing what we have and exiting.")
        (out_dir / "compute_stats.json").write_text(json.dumps(compute_rows, indent=2))
        await flow.shutdown()
        return

    # ============================================================================= design ===
    @flow.function_task
    async def task_design(dock_index: int, docked_oligomer: dict, geometry: dict):
        _t0 = time.monotonic()
        _task_setup(cage_designer_path)
        from CageFlow import _design_one_interface
        import records as _records

        residue_sets = _records.interface_residue_sets(geometry)
        dock_out_dir = out_dir / f"dock_{dock_index}"
        mpnn_force_cpu = config.get("mpnn_force_cpu", False)

        result = _design_one_interface(
            docked_oligomer, residue_sets, config, str(dock_out_dir), mpnn_dir,
            bias=config.get("bias"), force_cpu=mpnn_force_cpu,
        )
        stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
        return dock_index, result, stat

    design_tasks = [
        task_design(i, docked_oligomers[i], geometries[i]) for i in selected_indices
    ]
    design_results = await asyncio.gather(*design_tasks)

    designed_oligomers: dict[int, list] = {}
    design_stats = []
    for dock_index, result, stat in design_results:
        design_stats.append(stat)
        geometry = geometries[dock_index]
        if result["ok"]:
            designed_oligomers[dock_index] = result["designs"]
            summary_status, design_summary = "designed", CageFlow._summarize_design(result["designs"])
        else:
            summary_status, design_summary = "design_failed", None
        CageFlow.append_history(out_dir, records.dock_outcome(
            dock_index, docked_oligomers[dock_index], geometry, status=summary_status,
            design_summary=design_summary, error=result.get("error")))
    compute_rows.append(_compute_row(
        "design_interface", "cpu" if config.get("mpnn_force_cpu") else "gpu", design_stats))
    print(f"[design] {len(designed_oligomers)}/{len(selected_indices)} dock(s) designed")

    # ======================================================================= score_designs ===
    @flow.function_task
    async def task_score_designs(docked_oligomers: list[dict], geometries: list[dict]):
        _t0 = time.monotonic()
        _task_setup(cage_designer_path)
        import CageFlow as _cf
        scored = _cf.score_designs(
            designed_oligomers, docked_oligomers, geometries, config, str(out_dir))
        stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
        return scored, stat

    scored_designs, score_designs_stat = await task_score_designs(docked_oligomers, geometries)
    compute_rows.append(_compute_row("score_designs", "cpu", [score_designs_stat]))

    # ============================================================================ rosetta ===
    # This whole block is a line-for-line port of score_with_rosetta()'s pre/post-dispatch
    # body (CageFlow.py:2382-2621) -- NOT a reconstruction from memory. An earlier draft of
    # this file rebuilt the aggregation itself and silently dropped her per-dock sequence
    # dedup in the process; this version restores it, because "copy her logic exactly" is the
    # actual requirement here, not "produce an equivalent-looking result."
    import postredesign_selection as _pds
    import rosetta_scoring
    import symmdef
    from helpers.pdb import chains_to_design
    from helpers.symmdef_io import write_symmdef

    two_component = bool(designed_oligomers) and records.is_two_component(
        docked_oligomers, list(designed_oligomers))
    top_fraction = config.get("top_fraction_for_score", 0.75)
    cutoff = config.get("rosetta_score_cutoff", 0.5)
    chi_min = config.get("rosetta_chi_min", False)
    rosetta_seed = config.get("rosetta_seed")
    thread_mode = config.get("thread_mode", "replace")
    if thread_mode not in CageFlow.THREAD_MODES:
        raise ValueError(f"config thread_mode={thread_mode!r} is not one of {CageFlow.THREAD_MODES}")
    relax = CageFlow._reject_rosetta_relax(config)

    per_dock_state = {
        i: {"per_seq_metrics": [rosetta_scoring.zero_metrics() for _ in designs], "scored_values": []}
        for i, designs in designed_oligomers.items()
    }

    # The group that gets the expensive analyses -- the SAME group fold_oligomer folds and
    # export_af3_jobs writes, so a Rosetta number and a fold always describe the same molecule.
    selected = {i: set(js) for i, js in _pds.select(designed_oligomers, config).items()}
    selection_info = _pds.describe(config)

    # Per-dock precompute (chain_ids/design_positions/symm_file for one-component; chains_a/
    # chains_b/mpnn_cfg for two-component), done ONCE per dock here in the parent -- exactly
    # where her code does it, not redundantly inside every per-sequence task.
    dock_precompute: dict[int, dict] = {}
    seen_seq: dict[tuple[int, str], tuple[int, int]] = {}
    duplicate_of: dict[tuple[int, int], tuple[int, int]] = {}
    rosetta_items: list[tuple[int, int]] = []
    for i, designs in designed_oligomers.items():
        docked_oligomer = docked_oligomers[i]
        if two_component:
            mpnn_cfg_path = out_dir / f"dock_{i}" / "mpnn_input.json"
            dock_precompute[i] = {
                "chains_a": docked_oligomer["chains_a"], "chains_b": docked_oligomer["chains_b"],
                "mpnn_cfg": json.loads(mpnn_cfg_path.read_text()),
            }
        else:
            assembly_pdb = docked_oligomer["assembly_pdb"]
            chain_ids = chains_to_design(assembly_pdb)
            design_positions = records.flat_interface_resnums(geometries[i])
            symm_file = write_symmdef(
                str(out_dir), i, docked_oligomer["copies"], docked_oligomer["coset_reps"],
                config["symmetry"], config.get("contact_dist", 8.0),
                build_fn=symmdef.write_point_group_symmdef)
            # assembly_pdb is deliberately NOT stored here -- it is raw PDB text and `pre` is
            # shipped once per sequence task. The child re-reads it from its dock file instead.
            dock_precompute[i] = {
                "chain_ids": chain_ids, "design_positions": design_positions,
                "symm_file": symm_file,
            }

        for j, d in enumerate(designs):
            if j not in selected[i]:
                continue
            if d["score"] is None or d["score"] > cutoff:
                continue
            # Dedup per dock, NOT globally -- the same sequence on a different dock forms a
            # different interface and genuinely scores differently. See her comment at
            # CageFlow.py:2156-2164 for the measured example (-8.17 vs -5.87, same sequence).
            first = seen_seq.get((i, d["seq"]))
            if first is not None:
                duplicate_of[(i, j)] = first
                continue
            seen_seq[(i, d["seq"])] = (i, j)
            rosetta_items.append((i, j))

    n_designed = sum(len(d) for d in designed_oligomers.values())
    print(f"[rosetta] scoring {len(rosetta_items)} sequence(s) "
          f"(postredesign_selection {selection_info['method']}) that cleared cutoff={cutoff} "
          f"(of {n_designed} total designed"
          f"{f'; {len(duplicate_of)} duplicate(s) reused, not rescored' if duplicate_of else ''})")

    @flow.function_task
    async def task_rosetta(dock_index: int, list_index: int, docked_oligomer: dict, seq: str,
                           pre: dict):
        _t0 = time.monotonic()
        _task_setup(cage_designer_path)
        from CageFlow import _score_one_sequence_rosetta, _score_one_sequence_rosetta_2c

        if two_component:
            result = _score_one_sequence_rosetta_2c(
                docked_oligomer, seq, pre["chains_a"], pre["chains_b"], pre["mpnn_cfg"],
                chi_min, relax, rosetta_seed, thread_mode)
        else:
            result = _score_one_sequence_rosetta(
                docked_oligomer["assembly_pdb"], seq, pre["chain_ids"], pre["design_positions"],
                docked_oligomer, pre["symm_file"], chi_min, relax, seed=rosetta_seed)
        stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
        return dock_index, list_index, result, stat

    rosetta_tasks = [
        task_rosetta(i, j, docked_oligomers[i], designed_oligomers[i][j]["seq"],
                     dock_precompute[i])
        for i, j in rosetta_items
    ]
    rosetta_task_results = await asyncio.gather(*rosetta_tasks) if rosetta_tasks else []

    rosetta_stats = []
    scored_by_key: dict[tuple[int, int], dict] = {}
    for dock_index, list_index, result, stat in rosetta_task_results:
        rosetta_stats.append(stat)
        if result["ok"]:
            per_dock_state[dock_index]["per_seq_metrics"][list_index] = result["metrics"]
            per_dock_state[dock_index]["scored_values"].append(result["metrics"]["dG_separated"])
            scored_by_key[(dock_index, list_index)] = result["metrics"]
        else:
            print(f"[rosetta] dock {dock_index} seq {list_index} FAILED: {result['error']}")

    # Fan each skipped duplicate's result back out from the copy that did run -- same as her
    # code: per_seq_metrics stays dense, group_ddg is bit-identical to scoring them separately.
    for (i, j), first_key in duplicate_of.items():
        metrics = scored_by_key.get(first_key)
        if metrics is None:
            continue
        per_dock_state[i]["per_seq_metrics"][j] = metrics
        per_dock_state[i]["scored_values"].append(metrics["dG_separated"])

    rosetta_scores: dict[int, dict] = {}
    for i, designs in designed_oligomers.items():
        per_seq_metrics = per_dock_state[i]["per_seq_metrics"]
        per_seq_ddg = [m["dG_separated"] for m in per_seq_metrics]
        scored_values = per_dock_state[i]["scored_values"]
        group_ddg = records.group_score(scored_values, top_fraction)
        rosetta_scores[i] = {"per_seq_ddg": per_seq_ddg, "per_seq_metrics": per_seq_metrics,
                              "group_ddg": group_ddg}
        print(f"[rosetta] dock {i}: {len(scored_values)}/{len(designs)} sequences cleared "
              f"cutoff={cutoff} and were scored; group_ddg={group_ddg}")
        CageFlow.append_history(out_dir, records.dock_outcome(
            i, docked_oligomers[i], geometries[i], status="rosetta_scored",
            design_summary={
                **(scored_designs.get(i) or {}),
                "per_seq_ddg": per_seq_ddg, "per_seq_metrics": per_seq_metrics,
                "group_ddg": group_ddg, "n_scored": len(scored_values),
                "rosetta_score_cutoff": cutoff, "selected_indices": sorted(selected[i]),
                "postredesign_selection": selection_info, "rosetta_chi_min": chi_min,
                "rosetta_relax": relax, "thread_mode": thread_mode,
            }))
    if rosetta_stats:
        compute_rows.append(_compute_row("score_with_rosetta", "cpu", rosetta_stats))

    # =============================================================================== fold ===
    # Line-for-line port of fold_oligomer()'s body (CageFlow.py:3375-3583). An earlier draft
    # of this file passed ProteinMPNN's raw "/"-joined multi-chain string straight to
    # ColabFold -- exactly the documented production failure in her own docstring (run
    # 9759319: "Invalid character in the sequence: /", 3 minutes wasted fetching an MSA
    # first). _oligomer_chain_seq's one-chain extraction below is not optional.
    import native_gaps
    from helpers.pdb import chains_to_design

    fold_results: dict[int, dict] = {}
    if not config.get("fold_oligomers", False):
        print("[fold] skipped (fold_oligomers is off)")
    elif not designed_oligomers:
        print("[fold] skipped (nothing designed)")
    else:
        fold_selection = CageFlow.postredesign_selection.select(designed_oligomers, config)
        fold_unit = config.get("fold_unit", "cage")
        if fold_unit not in CageFlow.FOLD_UNITS:
            raise ValueError(f"config fold_unit={fold_unit!r} is not one of {CageFlow.FOLD_UNITS}")
        restore = native_gaps.gap_restorer(config)

        fold_pre: dict[tuple[int, int], dict] = {}
        dropped_by_fold: dict[tuple[int, int], int] = {}
        submitted_for_seq: dict[str, tuple[int, int]] = {}
        fold_duplicate_of: dict[tuple[int, int], tuple[int, int]] = {}
        fold_items: list[tuple[int, int]] = []
        for i, designs in designed_oligomers.items():
            docked_oligomer = docked_oligomers[i]
            if records.is_two_component([docked_oligomer]):
                continue
            native_pdb_text = docked_oligomer["copies"][0]
            n_chains = len(chains_to_design(native_pdb_text))
            if fold_unit == "cage":
                n_chains = len(chains_to_design(docked_oligomer["assembly_pdb"]))
                native_pdb_text = None
            for j in fold_selection.get(i, []):
                seq = CageFlow._oligomer_chain_seq(designs[j]["seq"], i, j)
                seq, n_dropped = native_gaps.foldable_sequence(seq, restore)
                if n_dropped:
                    dropped_by_fold[(i, j)] = n_dropped
                first = submitted_for_seq.get(seq)
                if first is not None:
                    fold_duplicate_of[(i, j)] = first
                    continue
                submitted_for_seq[seq] = (i, j)
                fold_pre[(i, j)] = {"seq": seq, "n_chains": n_chains, "native_pdb_text": native_pdb_text}
                fold_items.append((i, j))

        if dropped_by_fold:
            print(f"[fold] {len(dropped_by_fold)} candidate(s) had unresolved positions with no "
                  f"residue to restore ({sorted(set(dropped_by_fold.values()))} per sequence) -- "
                  f"dropped from the sequence sent to the predictor. Harmless for a numbering "
                  f"skip; for a real unresolved loop set restore_native_gaps=true.")

        # DELIBERATELY NOT PORTED: her fold_mode=="array" branch (CageFlow.py:3497), which
        # bypasses the executor entirely and dispatches a SLURM job array, returning
        # immediately. That is a second, competing scheduler -- exactly what this file exists
        # to replace -- so it is refused rather than silently ignored.
        if config.get("fold_mode", "inline") == "array":
            raise NotImplementedError(
                "config fold_mode='array' is not supported under the AsyncFlow orchestrator: "
                "it dispatches its own SLURM job array, which would run a second scheduler "
                "alongside AsyncFlow. Use fold_mode='inline' (the default).")

        if not fold_items:
            print("[fold] no foldable sequences (all docks two-component, or none selected)")
        else:
            print(f"[fold] predicting {len(fold_items)} sequence(s) with ColabFold "
                  f"(unit={fold_unit}"
                  f"{f'; {len(fold_duplicate_of)} duplicate(s) reused, not refolded' if fold_duplicate_of else ''})")

            @flow.function_task
            async def task_fold(dock_index: int, list_index: int, pre: dict):
                _t0 = time.monotonic()
                _task_setup(cage_designer_path)
                from CageFlow import _fetch_one_msa, _fold_one_sequence

                # Before BOTH ColabFold subprocesses, not just the predictor. `_fetch_one_msa`
                # also shells out to colabfold_python (fold.fetch_msa -> colabfold_msa.py), so
                # popping between the two -- as this did until job 24051 -- leaves the MSA
                # fetch inheriting PYTHONPATH, which kills it. The failure is silent: the
                # fetch returns ok=False, we fall through with a3m_path=None, and ColabFold
                # computes its own MSA inline (her documented downgrade path). The only
                # visible trace is `query.a3m` + `query_env/` in the fold dir where Track A
                # has `msa.a3m`. Track A pops before fold_oligomer() for exactly this reason;
                # see ../original/run_original.py's module docstring.
                # Safe here: the imports above resolve via _task_setup's sys.path, not
                # PYTHONPATH. Scoped to this task's own process -- no restore needed.
                os.environ.pop("PYTHONPATH", None)

                fold_out_dir = out_dir / f"dock_{dock_index}" / f"fold_seq_{list_index}"
                msa_mode = config.get("fold_msa_mode", "mmseqs2_uniref_env")
                a3m_path = None
                if msa_mode != "single_sequence":
                    msa_result = _fetch_one_msa(pre["seq"], pre["n_chains"], str(fold_out_dir), config)
                    if msa_result["ok"]:
                        a3m_path = msa_result["a3m_path"]
                    else:
                        # Never swallow this again: it silently changes which MSA the
                        # prediction is built on, which is a science difference, not a retry.
                        print(f"[fold] WARNING dock {dock_index} seq {list_index}: MSA prefetch "
                              f"failed ({msa_result['error']}); ColabFold will compute its own "
                              f"MSA inline. Results will not match a prefetched-MSA run.",
                              flush=True)

                result = _fold_one_sequence(
                    pre["seq"], pre["n_chains"], pre["native_pdb_text"], str(fold_out_dir),
                    config, a3m_path=a3m_path)
                stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
                return dock_index, list_index, result, stat

            fold_tasks = [task_fold(i, j, fold_pre[(i, j)]) for i, j in fold_items]
            fold_task_results = await asyncio.gather(*fold_tasks)

            fold_stats = []
            for dock_index, list_index, result, stat in fold_task_results:
                fold_stats.append(stat)
                fold_results.setdefault(dock_index, {})[list_index] = result
                if result["ok"]:
                    print(f"[fold] dock {dock_index} seq {list_index}: rmsd={result['rmsd']} "
                          f"pLDDT={result['mean_plddt']} ipTM={result['iptm']}")
                else:
                    print(f"[fold] dock {dock_index} seq {list_index} FAILED: {result['error']}")

            # Fan each skipped duplicate's result back out from the copy that did run.
            for (i, j), first_key in fold_duplicate_of.items():
                first_result = fold_results.get(first_key[0], {}).get(first_key[1])
                if first_result is not None:
                    fold_results.setdefault(i, {})[j] = dict(first_result, reused_from=list(first_key))

            for i, per_seq in fold_results.items():
                ok = [r for r in per_seq.values() if r["ok"] and r["rmsd"] is not None]
                CageFlow.append_history(out_dir, records.dock_outcome(
                    i, docked_oligomers[i], geometries[i], status="folded",
                    design_summary={
                        "fold": {str(j): r for j, r in per_seq.items()},
                        "n_folded": len(per_seq),
                        "best_rmsd": min((r["rmsd"] for r in ok), default=None),
                        "best_iptm": max((r["iptm"] for r in per_seq.values()
                                          if r["ok"] and r["iptm"] is not None), default=None),
                        "fold_msa_mode": config.get("fold_msa_mode", "mmseqs2_uniref_env"),
                        "fold_model_type": config.get("fold_model_type", "alphafold2_multimer_v3"),
                    }))
            if fold_stats:
                compute_rows.append(_compute_row("fold_oligomer", "gpu", fold_stats))

    # ========================================================================= export_af3 ===
    @flow.function_task
    async def task_export_af3(docked_oligomers: list[dict]):
        _t0 = time.monotonic()
        _task_setup(cage_designer_path)
        import CageFlow as _cf
        manifest = _cf.export_af3_jobs(
            designed_oligomers, docked_oligomers, rosetta_scores, config, str(out_dir))
        stat = {"elapsed_s": time.monotonic() - _t0, "host": socket.gethostname()}
        return manifest, stat

    manifest, export_stat = await task_export_af3(docked_oligomers)
    compute_rows.append(_compute_row("export_af3_jobs", "cpu", [export_stat]))
    print(f"[export_af3] {len(manifest)} job(s) exported")

    # ============================================================================= wrap up ===
    sequence_table = CageFlow.build_sequence_table(out_dir)
    sequence_table.to_csv(out_dir / "qualifying_sequences.csv", index=False)
    CageFlow.style_sequence_table(sequence_table).to_html(out_dir / "qualifying_sequences.html")

    (out_dir / "compute_stats.json").write_text(json.dumps(compute_rows, indent=2))

    n_folded = sum(1 for per_seq in fold_results.values() for r in per_seq.values() if r.get("ok"))
    print(f"\nTrack B run complete: {len(designed_oligomers)} dock(s) designed, "
          f"{len(rosetta_items)} sequence(s) Rosetta-scored, {n_folded} fold(s) succeeded.")

    await flow.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
