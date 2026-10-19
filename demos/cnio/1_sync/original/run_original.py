"""Track A reference run: her exact pipeline order, replicated outside run_cage_flow().

Replaces an earlier version of this run that called CageFlow.run_cage_flow(config) directly.
That version had a real bug, not just a style preference: it popped PYTHONPATH right after
`import CageFlow`, before run_cage_flow() even started -- which strips PYTHONPATH from every
stage's subprocess calls, not just fold's. Confirmed by a real failure (job 24016, 2026-10-18):
ProteinMPNN's own helper subprocess (parse_multiple_chains.py, which also runs under
~/venvs/cageflow_unified/bin/python3 and has no numpy of its own -- confirmed separately that
`env -u PYTHONPATH cageflow_unified/bin/python3 -c "import numpy"` fails outright) crashed with
`ModuleNotFoundError: No module named 'numpy'` during design_interface(), not fold. The job exited
0 (nothing raised past design's own try/except) but designed zero sequences.

The actual fix: run_cage_flow() is one monolithic call with no hook to pop PYTHONPATH at exactly
the right moment (between score_with_rosetta and fold_oligomer) from outside -- its own comment
admits this. isolation/7_fold_oligomer/main.py already proves the correct alternative: call the
documented stage functions directly, in the exact order _run_cage_flow_steps() uses (reproduced
below, including her bookkeeping -- reset_history, logging setup, budget/compute/allocation
reports, the sequence table), with the pop at the one point that's actually safe.

Zero changes to CageFlow.py. Every call here is one of her documented, unmodified top-level
functions -- this file only reproduces the ORDER and BOOKKEEPING she already has in
run_cage_flow()/_run_cage_flow_steps(), because that orchestration offers no seam to inject the
one line (the PYTHONPATH pop) that has to happen between two of her calls.
"""

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, "/home/javidsegura/software/CageFlow")
import CageFlow  # noqa: E402

CONFIG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "config.json"


def main():
    config = json.loads(CONFIG_PATH.read_text())
    out_dir = Path(config.get("out_dir", "cageflow_output"))
    out_dir.mkdir(parents=True, exist_ok=True)

    # Same bookkeeping run_cage_flow() does, reproduced verbatim -- see its docstring for why
    # both resets matter (append-only history.jsonl / append-mode FileHandler otherwise
    # interleave a second run's records after a first run's).
    CageFlow.reset_history(out_dir)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.FileHandler(out_dir / "cageflow.log", mode="w"),
            logging.StreamHandler(),
        ],
    )

    executor = CageFlow.build_executor(config)
    budget = CageFlow.build_budget(config)
    mpnn_dir = config.get("mpnn_dir", "ProteinMPNN")

    try:
        # Step 1: Docking.
        if config.get("scaffold_pdb_b"):
            docked_oligomers = CageFlow.dock_two_component(config, executor, budget)
        else:
            docked_oligomers = CageFlow.dock_oligomer(config, executor, budget)

        # Step 2: Geometry.
        geometries = CageFlow.evaluate_geometry(docked_oligomers, config, executor, budget, out_dir)

        # Step 2.5: Selection.
        selected_indices = CageFlow.select_top_docks(docked_oligomers, geometries, config, out_dir)

        # Step 3/4/4.5: Design + score. reactive_scoring is not in our frozen config, so always
        # the batch path -- design ALL selected docks, then score ALL qualifying sequences.
        bias = config.get("bias")
        designed_oligomers = CageFlow.design_interface(
            docked_oligomers, geometries, selected_indices, config, executor, budget, out_dir,
            mpnn_dir, bias=bias,
        )
        scored_designs = CageFlow.score_designs(
            designed_oligomers, docked_oligomers, geometries, config, out_dir)
        rosetta_scores = CageFlow.score_with_rosetta(
            scored_designs, designed_oligomers, docked_oligomers, geometries, config, executor,
            budget, out_dir)

        # ---- THE FIX: pop PYTHONPATH here, and only here -- after every stage that needs it
        # (design's ProteinMPNN subprocess, Rosetta scoring) has already run, and before the one
        # stage that must NOT have it (fold_oligomer's ColabFold subprocess, which spawns its own
        # bundled python3.10 and inherits os.environ wholesale). ----
        os.environ.pop("PYTHONPATH", None)

        # Step 5/6: Fold.
        fold_results = CageFlow.fold_oligomer(
            designed_oligomers, docked_oligomers, geometries, rosetta_scores, config, executor,
            budget, out_dir)

        # Step 6b: AF3 export -- unconditional, same group fold_oligomer used.
        CageFlow.export_af3_jobs(designed_oligomers, docked_oligomers, rosetta_scores, config, out_dir)

        # Step 7: whole-cage fold -- stub, raises if requested. Not set in our config.
        if config.get("fold_entire_cage", False):
            raise NotImplementedError(
                "fold_entire_cage() is a stub -- a T-symmetric cage is 12+ chains, "
                "which is a separate problem from fold_oligomer()'s single oligomer.")

        # Step 9: sequence table.
        sequence_table = CageFlow.build_sequence_table(out_dir)
        sequence_table.to_csv(out_dir / "qualifying_sequences.csv", index=False)
        CageFlow.style_sequence_table(sequence_table).to_html(out_dir / "qualifying_sequences.html")
        n_docks_in_table = (sequence_table["dock_index"].nunique()
                            if not sequence_table.empty else 0)
        with_rosetta = (not sequence_table.empty
                        and sequence_table["dG_separated"].notna().any())
        logging.info(
            f"  Sequence table: {len(sequence_table)} sequences across {n_docks_in_table} docks "
            f"({'with' if with_rosetta else 'no'} Rosetta metrics) -- wrote "
            f"{out_dir / 'qualifying_sequences.csv'}")

        # Step 10: budget + compute.
        budget_summary = budget.summary()
        (out_dir / "budget_summary.json").write_text(json.dumps(budget_summary, indent=2))
        compute_report = budget.compute_report()
        (out_dir / "compute_stats.json").write_text(json.dumps(compute_report, indent=2))

        result = {
            "docked_oligomers": docked_oligomers, "geometries": geometries,
            "designed_oligomers": designed_oligomers, "scored_designs": scored_designs,
            "rosetta_scores": rosetta_scores, "fold_results": fold_results,
            "sequence_table": sequence_table, "budget": budget_summary,
        }
    finally:
        executor.shutdown()

    # AFTER shutdown, same as run_cage_flow() -- so sacct has a final Elapsed to report.
    result["allocation"] = CageFlow._write_allocation_report(
        executor, budget_summary=result.get("budget"), out_dir=out_dir)

    n_designed = sum(len(d) for d in result["designed_oligomers"].values())
    n_folded = sum(len(f) for f in result["fold_results"].values())
    print(f"\nTrack A reference run complete: {n_designed} sequence(s) designed, "
          f"{n_folded} fold(s) attempted. See {out_dir}/cageflow.log for full detail.")


if __name__ == "__main__":
    main()
