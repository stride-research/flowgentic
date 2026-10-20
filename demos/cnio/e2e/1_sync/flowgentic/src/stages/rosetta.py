"""Rosetta stage: scores each cleared-cutoff sequence with Rosetta, per dock, producing
state.rosetta_scores.

Line-for-line port of score_with_rosetta()'s pre/post-dispatch body (CageFlow.py:2382-2621)
-- NOT a reconstruction from memory. An earlier draft of this file rebuilt the aggregation
itself and silently dropped her per-dock sequence dedup in the process; this version
restores it, because "copy her logic exactly" is the actual requirement here, not "produce
an equivalent-looking result."
"""
from __future__ import annotations

import asyncio
import json
import logging

import flowgentic.metrics as fg

from state import PipelineState


async def run(flow, config: dict, cage_designer_path: str, out_dir, state: PipelineState) -> PipelineState:
    import CageFlow
    import records

    docked_oligomers = state.docked_oligomers
    geometries = state.geometries
    designed_oligomers = state.designed_oligomers
    scored_designs = state.scored_designs

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
    logging.info(f"[rosetta] scoring {len(rosetta_items)} sequence(s) "
          f"(postredesign_selection {selection_info['method']}) that cleared cutoff={cutoff} "
          f"(of {n_designed} total designed"
          f"{f'; {len(duplicate_of)} duplicate(s) reused, not rescored' if duplicate_of else ''})")

    @fg.task(flow, name="score_with_rosetta",
             description="Rosetta interface scoring for one cleared-cutoff sequence")
    async def task_rosetta(dock_index: int, list_index: int, docked_oligomer: dict, seq: str,
                           pre: dict):
        from CageFlow import _score_one_sequence_rosetta, _score_one_sequence_rosetta_2c

        if two_component:
            result = _score_one_sequence_rosetta_2c(
                docked_oligomer, seq, pre["chains_a"], pre["chains_b"], pre["mpnn_cfg"],
                chi_min, relax, rosetta_seed, thread_mode)
        else:
            result = _score_one_sequence_rosetta(
                docked_oligomer["assembly_pdb"], seq, pre["chain_ids"], pre["design_positions"],
                docked_oligomer, pre["symm_file"], chi_min, relax, seed=rosetta_seed)
        return dock_index, list_index, result

    rosetta_tasks = [
        task_rosetta(i, j, docked_oligomers[i], designed_oligomers[i][j]["seq"],
                     dock_precompute[i])
        for i, j in rosetta_items
    ]
    rosetta_task_results = await asyncio.gather(*rosetta_tasks) if rosetta_tasks else []

    scored_by_key: dict[tuple[int, int], dict] = {}
    for dock_index, list_index, result in rosetta_task_results:
        if result["ok"]:
            per_dock_state[dock_index]["per_seq_metrics"][list_index] = result["metrics"]
            per_dock_state[dock_index]["scored_values"].append(result["metrics"]["dG_separated"])
            scored_by_key[(dock_index, list_index)] = result["metrics"]
        else:
            logging.warning(f"[rosetta] dock {dock_index} seq {list_index} FAILED: {result['error']}")

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
        logging.info(f"[rosetta] dock {i}: {len(scored_values)}/{len(designs)} sequences cleared "
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

    state.rosetta_scores = rosetta_scores
    return state
