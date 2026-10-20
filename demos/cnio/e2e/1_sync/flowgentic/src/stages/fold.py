"""Fold stage: predicts structure with ColabFold for each selected designed sequence,
producing state.fold_results.

Line-for-line port of fold_oligomer()'s body (CageFlow.py:3375-3583). An earlier draft of
this file passed ProteinMPNN's raw "/"-joined multi-chain string straight to ColabFold --
exactly the documented production failure in her own docstring (run 9759319: "Invalid
character in the sequence: /", 3 minutes wasted fetching an MSA first).
_oligomer_chain_seq's one-chain extraction below is not optional.
"""
from __future__ import annotations

import asyncio
import logging
import os

import flowgentic.metrics as fg

from state import PipelineState


async def run(flow, config: dict, cage_designer_path: str, out_dir, state: PipelineState) -> PipelineState:
    import CageFlow
    import records

    docked_oligomers = state.docked_oligomers
    geometries = state.geometries
    designed_oligomers = state.designed_oligomers

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
        logging.info("[fold] skipped (fold_oligomers is off)")
    elif not designed_oligomers:
        logging.info("[fold] skipped (nothing designed)")
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
            logging.info(f"[fold] {len(dropped_by_fold)} candidate(s) had unresolved positions with no "
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
            logging.info("[fold] no foldable sequences (all docks two-component, or none selected)")
        else:
            logging.info(f"[fold] predicting {len(fold_items)} sequence(s) with ColabFold "
                  f"(unit={fold_unit}"
                  f"{f'; {len(fold_duplicate_of)} duplicate(s) reused, not refolded' if fold_duplicate_of else ''})")

            @fg.task(flow, name="fold_oligomer", resource="gpu",
                     description="ColabFold structure prediction for one designed sequence")
            async def task_fold(dock_index: int, list_index: int, pre: dict):
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
                        # logging, not print: this runs inside a Dragon child, and _prepare()
                        # (called by flowgentic.metrics.task's wrapper, before this body runs)
                        # has already pointed this process's root logger at cageflow.log --
                        # same file the parent and every other child write to, so this warning
                        # is not only visible in sbatch.out.
                        logging.warning(f"[fold] dock {dock_index} seq {list_index}: MSA prefetch "
                              f"failed ({msa_result['error']}); ColabFold will compute its own "
                              f"MSA inline. Results will not match a prefetched-MSA run.")

                return dock_index, list_index, _fold_one_sequence(
                    pre["seq"], pre["n_chains"], pre["native_pdb_text"], str(fold_out_dir),
                    config, a3m_path=a3m_path)

            fold_tasks = [task_fold(i, j, fold_pre[(i, j)]) for i, j in fold_items]
            fold_task_results = await asyncio.gather(*fold_tasks)

            for dock_index, list_index, result in fold_task_results:
                fold_results.setdefault(dock_index, {})[list_index] = result
                if result["ok"]:
                    logging.info(f"[fold] dock {dock_index} seq {list_index}: rmsd={result['rmsd']} "
                          f"pLDDT={result['mean_plddt']} ipTM={result['iptm']}")
                else:
                    logging.warning(f"[fold] dock {dock_index} seq {list_index} FAILED: {result['error']}")

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

    state.fold_results = fold_results
    return state
