"""Step 8: export_af3_jobs, in isolation. See SUCCESS.md for what this proves.

New in 0d1650a -- did not exist when steps 1-7 were first written. Unconditional in the real
pipeline (CageFlow._run_cage_flow_steps calls it as "Step 6b", right after fold_oligomer,
regardless of whether fold_oligomers is on): a run that never touches the GPU, or whose folds
time out, is exactly the run whose sequences most need an AF3 job file instead. It is a sibling
of fold_oligomer, not its successor -- both are fed by the SAME postredesign_selection group
(fold_candidates()), so this isolation step only needs what step 6 already builds (designed,
docked, rosetta_scores), not step 7's fold_results.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import check, load_config, write_artifact  # noqa: E402

import CageFlow  # noqa: E402

config = load_config(__file__, max_materialized=2, n_docks=1, num_seq_per_target=1)
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

check("at least one dock has Rosetta scores to export", len(rosetta_scores) > 0,
      f"got {len(rosetta_scores)}")

manifest = CageFlow.export_af3_jobs(designed, docks, rosetta_scores, config, config["out_dir"])

check("export_af3_jobs returned a non-empty manifest", len(manifest) > 0,
      "designed_oligomers was non-empty but manifest came back []")

af3_dir = Path(config["out_dir"]) / "af3"
check("af3_server_ALL.json was written", (af3_dir / "af3_server_ALL.json").exists(),
      str(af3_dir / "af3_server_ALL.json"))
check("manifest.csv was written", (af3_dir / "manifest.csv").exists(),
      str(af3_dir / "manifest.csv"))

for row in manifest:
    job_name = row["job_name"]
    check(f"job {job_name}: a real per-job JSON file was written",
          (af3_dir / "af3_local" / f"{job_name}.json").exists(),
          str(af3_dir / "af3_local" / f"{job_name}.json"))
    check(f"job {job_name}: token count is a positive int",
          isinstance(row["tokens"], int) and row["tokens"] > 0, str(row.get("tokens")))

write_artifact(__file__, {
    "n_jobs": len(manifest),
    "manifest": manifest,
})

for row in manifest:
    print(f"\n{row['job_name']}: dock={row['dock_index']} sample={row['sample']} "
          f"dG={row['dG_separated']} tokens={row['tokens']}")
