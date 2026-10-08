"""Step 7: fold_oligomer (real ColabFold, MSA fetch + structure inference).

See SUCCESS.md in this folder for what this proves and why this is one
step, not the two (7a/7b) an earlier version split it into.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import check, load_config, write_artifact  # noqa: E402

import CageFlow  # noqa: E402

# Same n_docks=1/num_seq_per_target=1 reasoning as step 6: multi-dock and
# multi-sequence iteration are already proven in steps 3-5, and this
# chain pays Rosetta's ~134s/sequence cost too before fold even starts.
# fold_oligomers=true + fold_top_n (from the probe config, 1) keeps this
# to exactly one real GPU fold regardless.
config = load_config(__file__, max_materialized=2, n_docks=1, num_seq_per_target=1,
                     fold_oligomers=True)
executor = CageFlow.build_executor(config)
budget = CageFlow.build_budget(config)

docks = CageFlow.dock_oligomer(config, executor, budget)
geometries = CageFlow.evaluate_geometry(docks, config, executor, budget, config["out_dir"])
selected = CageFlow.select_top_docks(docks, geometries, config, config["out_dir"])
designed = CageFlow.design_interface(
    docks, geometries, selected, config, executor, budget,
    config["out_dir"], config["mpnn_dir"])
scored = CageFlow.score_designs(designed, docks, geometries, config, config["out_dir"])
rosetta_scores = CageFlow.score_with_rosetta(
    scored, designed, docks, geometries, config, executor, budget, config["out_dir"])

check("at least one dock has Rosetta scores to rank fold candidates by",
      len(rosetta_scores) > 0, f"got {len(rosetta_scores)}")


os.environ.pop("PYTHONPATH", None)

fold_results = CageFlow.fold_oligomer(
    designed, docks, geometries, rosetta_scores, config, executor, budget, config["out_dir"])

check("fold_oligomer returned results for at least one dock",
      len(fold_results) > 0,
      "fold_oligomers=true but nothing was folded" if not fold_results
      else f"got {len(fold_results)} dock(s)")

artifact = {}
for i, per_seq in sorted(fold_results.items()):
    check(f"dock {i}: fold_top_n={config.get('fold_top_n', 1)} sequence(s) were folded",
          len(per_seq) == config.get("fold_top_n", 1), f"got {len(per_seq)}")
    for j, result in sorted(per_seq.items()):
        check(f"dock {i} seq {j}: fold reported ok", result["ok"], str(result.get("error")))
        check(f"dock {i} seq {j}: a real PDB was written",
              result.get("pdb_path") and Path(result["pdb_path"]).exists(),
              str(result.get("pdb_path")))
        check(f"dock {i} seq {j}: mean_plddt is a real number",
              isinstance(result.get("mean_plddt"), float), str(result.get("mean_plddt")))
        check(f"dock {i} seq {j}: rmsd against the docked pose is a real number or a named failure reason",
              isinstance(result.get("rmsd"), float) or result.get("error"),
              f"rmsd={result.get('rmsd')}, error={result.get('error')}")
        artifact[f"dock_{i}_seq_{j}"] = {
            "pdb_path": result.get("pdb_path"),
            "mean_plddt": result.get("mean_plddt"),
            "ptm": result.get("ptm"),
            "iptm": result.get("iptm"),
            "rmsd": result.get("rmsd"),
            "error": result.get("error"),
        }

write_artifact(__file__, artifact)

for i, per_seq in sorted(fold_results.items()):
    for j, result in sorted(per_seq.items()):
        print(f"\ndock {i} seq {j}: mean_plddt={result.get('mean_plddt')} "
              f"iptm={result.get('iptm')} rmsd={result.get('rmsd')}")
