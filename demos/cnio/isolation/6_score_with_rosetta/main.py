"""Step 6: score_with_rosetta (the real PyRosetta batch function).

See SUCCESS.md in this folder for what this proves.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import check, load_config, write_artifact  # noqa: E402

import CageFlow  # noqa: E402

# n_docks=1 here, not 2 like steps 3-5 -- those steps exist specifically to
# prove "every dock gets checked, not just the first", which is already
# established. Rosetta scoring is the expensive step (~134s/sequence), so
# paying the multi-dock cost again here buys nothing new. max_materialized=2
# is the smallest value that still lets select_top_docks (n_docks=1) do real
# trimming, same reasoning as step 3. num_seq_per_target=1 for the same
# reason: steps 4-5 already proved every design in a batch gets handled,
# not just the first -- Rosetta scoring 1 real sequence instead of 4 cuts
# both ProteinMPNN and Rosetta time ~4x without reconstructing any input.
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

check("at least one dock has scored designs to Rosetta-score", len(scored) > 0, f"got {len(scored)}")

rosetta_scores = CageFlow.score_with_rosetta(
    scored, designed, docks, geometries, config, executor, budget, config["out_dir"])

check("score_with_rosetta returned a dict keyed by exactly the scored docks",
      isinstance(rosetta_scores, dict) and set(rosetta_scores) == set(scored),
      f"rosetta_scores={sorted(rosetta_scores)}, scored={sorted(scored)}")

for i, result in sorted(rosetta_scores.items()):
    per_seq_ddg = result["per_seq_ddg"]
    per_seq_metrics = result["per_seq_metrics"]
    group_ddg = result["group_ddg"]

    check(f"dock {i}: per_seq_metrics has one entry per designed sequence",
          len(per_seq_metrics) == len(designed[i]),
          f"{len(per_seq_metrics)} vs {len(designed[i])} designs")
    # per_seq_ddg is DENSE (one entry per design, 0.0 for any sequence that
    # missed the cutoff), so checking its length is meaningless -- it's
    # always == len(designed[i]). group_ddg is the real signal: _group_score
    # (records.py) returns None for an empty input, and score_with_rosetta()
    # only feeds it actually-scored values, so group_ddg is not None iff at
    # least one sequence cleared rosetta_score_cutoff.
    check(f"dock {i}: at least one sequence actually got scored by Rosetta",
          group_ddg is not None,
          "every sequence missed rosetta_score_cutoff or errored" if group_ddg is None
          else f"group_ddg={group_ddg}")
    check(f"dock {i}: dG_separated is a real number for every scored sequence",
          all(isinstance(m["dG_separated"], float) for m in per_seq_metrics),
          str(per_seq_metrics[0]))
    check(f"dock {i}: group_ddg is a real number",
          isinstance(group_ddg, float), str(group_ddg))

write_artifact(__file__, {
    "n_docks_scored": len(rosetta_scores),
    "docks": {
        str(i): {
            "per_seq_ddg": r["per_seq_ddg"],
            "group_ddg": r["group_ddg"],
        }
        for i, r in sorted(rosetta_scores.items())
    },
})

for i, r in sorted(rosetta_scores.items()):
    print(f"\ndock {i}: group_ddg={r['group_ddg']:.4f} over {len(r['per_seq_ddg'])} "
          f"Rosetta-scored sequence(s), per_seq_ddg={r['per_seq_ddg']}")
