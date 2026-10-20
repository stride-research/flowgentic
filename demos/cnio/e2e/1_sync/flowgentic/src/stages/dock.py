"""Dock stage: produces docked_oligomers for every downstream stage.

Handles both one-component docking (scaffold_pdb only -- scan_docking + prescore +
_materialize_dock) and two-component docking (scaffold_pdb + scaffold_pdb_b --
dock_two_component(), CageFlow.py:623-904) depending on whether config["scaffold_pdb_b"]
is set.

Everything this stage computes besides `docked_oligomers` itself (pdb_raw, canonical_axis,
coset_reps, R_align, com, axis_a/b, reps_a/b, ...) is purely internal setup/materialize
plumbing and never crosses to geometry/select/design/rosetta/fold -- those only ever
consume `docked_oligomers`. That is why it is the only field this stage writes to
PipelineState.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time

import flowgentic.metrics as fg

from state import PipelineState

#: How many prescore chunks to split a dock run's pose grid into -- mirrors her own
#: _chunk_prescore's chunking (see DESIGN.md's dock/prescore row), run as AsyncFlow tasks
#: instead of through _charge(). Read from the Slurm allocation rather than os.cpu_count():
#: these nodes have 512 logical CPUs but our sbatch asks for 8, and os.cpu_count() reports the
#: former -- which is exactly the mismatch that drained three of Laura's nodes when ProteinMPNN
#: sized its own pool that way (notes/handover_2026-10-12.md). Falls back to 8 only when run
#: outside a Slurm allocation.
N_PRESCORE_CHUNKS = int(os.environ.get("SLURM_CPUS_PER_TASK") or 8)


async def run(flow, config: dict, cage_designer_path: str, out_dir, state: PipelineState,
               cross_contact_dist: float, cross_clash_dist: float) -> PipelineState:
    """Dispatch to the one-component or two-component dock implementation and store
    the result as state.docked_oligomers.
    """
    import CageFlow

    if not config.get("scaffold_pdb_b"):
        # ================================================================== dock: setup + scan ===
        @fg.task(flow, name="dock_setup",
                 description="One-component dock setup: prepare scaffold, scan_docking")
        async def task_dock_setup():
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
                logging.info(f"[dock] harmonized chain residue ranges before docking: {dropped}")
            if skipped:
                logging.warning(f"[dock] grouped chains with incompatible residue numbering, left "
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

            return pdb_raw, canonical_axis, coset_reps, R_align, com, is_high_sym, solutions

        (pdb_raw, canonical_axis, coset_reps, R_align, com, is_high_sym,
         solutions) = await task_dock_setup()
        logging.info(f"[dock] scan_docking: {len(solutions)} candidate poses")

        # ===================================================================== dock: prescore ===
        max_mat = config.get("max_materialized")
        if max_mat is not None and len(solutions) > min(CageFlow.MATERIALIZE_FLOOR, int(max_mat)):
            chunk_size = math.ceil(len(solutions) / N_PRESCORE_CHUNKS)
            chunks = [solutions[i:i + chunk_size] for i in range(0, len(solutions), chunk_size)]

            @fg.task(flow, name="prescore",
                     description="Steric prescore for one chunk of candidate poses")
            async def task_prescore_chunk(chunk):
                from CageFlow import prescore_solutions
                return prescore_solutions(
                    pdb_raw, canonical_axis, coset_reps, R_align, com, chunk, config,
                    cage_designer_path, flip=config.get("flip", False))

            _t0 = time.monotonic()
            chunk_tasks = [task_prescore_chunk(c) for c in chunks]
            chunk_results = await asyncio.gather(*chunk_tasks)
            prescores = []
            for part in chunk_results:
                prescores.extend(part)

            n_keep = CageFlow._n_to_materialize(prescores, config)
            order = sorted(range(len(solutions)), key=CageFlow._prescore_rank_key(prescores, config))
            keep = sorted(order[:n_keep])
            n_ok = sum(1 for p in prescores if p["steric_ok"])
            logging.info(f"[dock] prescore: {len(solutions)} poses in {time.monotonic() - _t0:.1f}s "
                  f"({n_ok} sterically admissible); materializing {len(keep)}")
            solutions = [solutions[i] for i in keep]

        # ==================================================================== dock: materialize ===
        @fg.task(flow, name="dock_oligomer",
                 description="Materialize every kept pose into a full docked oligomer")
        async def task_materialize():
            from CageFlow import _materialize_dock
            flip = config.get("flip", False)
            return [
                _materialize_dock(pdb_raw, canonical_axis, coset_reps, R_align, com, is_high_sym,
                                   sol, cage_designer_path, flip)
                for sol in solutions
            ]

        docked_oligomers = await task_materialize()
        logging.info(f"[dock] materialized {len(docked_oligomers)} dock(s)")

    else:
        # ======================================================== dock: two-component (2c) ===
        # Faithful port of dock_two_component() (CageFlow.py:623-904). Downstream code
        # (geometry's cross_* branch, Rosetta's _2c scorer, fold's two-component skip)
        # already existed and was unreachable dead code until this branch; nothing past
        # this point needed to change to support it.
        @fg.task(flow, name="dock_setup",
                 description="Two-component dock setup: prepare both scaffolds, self-placement scan")
        async def task_dock_setup_2c():
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
                    logging.info(f"[dock_2c] harmonized component {label} chain residue ranges: {dropped}")
                if skipped:
                    logging.warning(f"[dock_2c] component {label} has grouped chains with incompatible "
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

            return (pdb_raw_a, pdb_raw_b, axis_a, axis_b, reps_a, reps_b, R_align_a, R_align_b,
                    com_a, com_b, pre_a, pre_b, interaction_cutoff, ref_coset_only, grid_size,
                    flip_a, flip_b)

        (pdb_raw_a, pdb_raw_b, axis_a, axis_b, reps_a, reps_b, R_align_a, R_align_b,
         com_a, com_b, pre_a, pre_b, interaction_cutoff, ref_coset_only, grid_size,
         flip_a, flip_b) = await task_dock_setup_2c()

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
        logging.info(f"[dock_2c] pair search: {len(pre_a)} x {len(pre_b)} = "
              f"{len(pre_a) * len(pre_b)} candidate pairs over {len(chunks_a)} chunk(s)")

        @fg.task(flow, name="dock_two_component_pair_search",
                 description="Cross-contact pair search for one chunk of component-A candidates")
        async def task_scan_2c_chunk(chunk_a: list, grid_size: int):
            from CageFlow import _scan_2c_chunk_worker
            return _scan_2c_chunk_worker(
                chunk_a, pre_b, interaction_cutoff, cross_clash_dist, cross_contact_dist,
                min_cross_contacts, config.get("w_quality", 1.0), config.get("w_clash", 1.0),
                config.get("w_radius", 0.1), config.get("w_cross_quality", 1.5),
                config.get("w_cross_clash", 1.0), grid_size, cage_designer_path, ref_coset_only)

        _t0 = time.monotonic()
        scan_tasks = [task_scan_2c_chunk(c, grid_size) for c in chunks_a]
        scan_results = await asyncio.gather(*scan_tasks)
        solutions = []
        for part in scan_results:
            solutions.extend(part)

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
        logging.info(f"[dock_2c] pair search: {len(solutions)} solution(s) in "
              f"{time.monotonic() - _t0:.1f}s")

        # Optional prescore, mirroring the one-component block above -- same chunking
        # pattern, her two-component prescore worker instead of the one-component one.
        max_mat = config.get("max_materialized")
        if max_mat is not None and len(solutions) > min(CageFlow.MATERIALIZE_FLOOR, int(max_mat)):
            chunk_size = math.ceil(len(solutions) / N_PRESCORE_CHUNKS)
            chunks = [solutions[i:i + chunk_size] for i in range(0, len(solutions), chunk_size)]

            @fg.task(flow, name="prescore",
                     description="Steric prescore for one chunk of two-component candidate poses")
            async def task_prescore_2c_chunk(chunk):
                from CageFlow import prescore_solutions_2c
                return prescore_solutions_2c(
                    pdb_raw_a, pdb_raw_b, axis_a, axis_b, reps_a, reps_b, R_align_a, R_align_b,
                    com_a, com_b, chunk, config, cage_designer_path, flip_a=flip_a, flip_b=flip_b)

            _t0 = time.monotonic()
            chunk_tasks = [task_prescore_2c_chunk(c) for c in chunks]
            chunk_results = await asyncio.gather(*chunk_tasks)
            prescores = []
            for part in chunk_results:
                prescores.extend(part)

            n_keep = CageFlow._n_to_materialize(prescores, config)
            order = sorted(range(len(solutions)), key=CageFlow._prescore_rank_key(prescores, config))
            keep = sorted(order[:n_keep])
            n_ok = sum(1 for p in prescores if p["steric_ok"])
            logging.info(f"[dock_2c] prescore: {len(solutions)} poses in {time.monotonic() - _t0:.1f}s "
                  f"({n_ok} sterically admissible); materializing {len(keep)}")
            solutions = [solutions[i] for i in keep]

        @fg.task(flow, name="dock_oligomer",
                 description="Materialize every kept pose into a full two-component docked oligomer")
        async def task_materialize_2c():
            from CageFlow import _materialize_dock_2c
            return [
                _materialize_dock_2c(pdb_raw_a, pdb_raw_b, axis_a, axis_b, reps_a, reps_b,
                                     R_align_a, R_align_b, com_a, com_b, sol, cage_designer_path,
                                     flip_a=flip_a, flip_b=flip_b)
                for sol in solutions
            ]

        docked_oligomers = await task_materialize_2c()
        logging.info(f"[dock_2c] materialized {len(docked_oligomers)} two-component dock(s)")


    state.docked_oligomers = docked_oligomers
    return state
