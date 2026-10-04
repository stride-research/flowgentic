"""Step 5: score_designs.

See SUCCESS.md in this folder for what this proves and what it does not
touch.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import check, load_config, write_artifact  # noqa: E402

import CageFlow  # noqa: E402

# Same reasoning as steps 3-4: need multiple docks for selection to do
# something, and a real design to score. Grid is still unreduced.
config = load_config(__file__, max_materialized=4, n_docks=2)
executor = CageFlow.build_executor(config)
budget = CageFlow.build_budget(config)

docks = CageFlow.dock_oligomer(config, executor, budget)
geometries = CageFlow.evaluate_geometry(docks, config, executor, budget, config["out_dir"])
selected = CageFlow.select_top_docks(docks, geometries, config, config["out_dir"])
designed = CageFlow.design_interface(
    docks, geometries, selected, config, executor, budget,
    config["out_dir"], config["mpnn_dir"])

check("at least one dock has designs to score", len(designed) > 0, f"got {len(designed)}")

scored = CageFlow.score_designs(designed, docks, geometries, config, config["out_dir"])

check("score_designs returned a dict keyed only by docks that were designed",
      isinstance(scored, dict) and set(scored) <= set(designed),
      f"keys={sorted(scored)}, designed={sorted(designed)}")
check("every designed dock got scored",
      set(scored) == set(designed), f"scored={sorted(scored)}, designed={sorted(designed)}")

# Checked for EVERY scored dock, not just the first -- docks differ in
# interface size and get genuinely different sequences, so one dock
# passing says nothing about the others.
for i, result in sorted(scored.items()):
    per_seq = result["per_seq_scores"]
    group = result["group_score"]

    check(f"dock {i}: per_seq_scores has one entry per designed sequence",
          len(per_seq) == len(designed[i]),
          f"{len(per_seq)} vs {len(designed[i])} designs")
    check(f"dock {i}: group_score is a real number",
          isinstance(group, float), str(type(group)))
    # Structural sanity, not re-deriving her formula: group_score is a mean
    # over SOME subset of per_seq_scores, so it cannot fall outside their
    # range. We don't check it equals our own recomputation of the mean --
    # that would mean reimplementing records.group_score() ourselves.
    check(f"dock {i}: group_score falls within the per-sequence score range",
          min(per_seq) <= group <= max(per_seq),
          f"group={group}, range=[{min(per_seq)}, {max(per_seq)}]")

write_artifact(__file__, {
    "n_docks_scored": len(scored),
    "docks": {
        str(i): {
            "per_seq_scores": r["per_seq_scores"],
            "group_score": r["group_score"],
        }
        for i, r in sorted(scored.items())
    },
})

for i, r in sorted(scored.items()):
    print(f"\ndock {i}: group_score={r['group_score']:.4f} "
          f"over {len(r['per_seq_scores'])} sequences {r['per_seq_scores']}")
