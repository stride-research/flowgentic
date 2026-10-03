"""Step 3: select_top_docks.

See SUCCESS.md in this folder for what this proves, what it does not
touch, and how the input was reduced without reducing the science.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import check, load_config, write_artifact  # noqa: E402

import CageFlow  # noqa: E402

# max_materialized=4 and n_docks=2: unlike steps 1-2, ranking/trimming
# over a SINGLE dock is a no-op -- this step only does something
# observable with more than one candidate to choose among. The search
# grid is still scanned at full density either way; we are only keeping
# more of the already-ranked candidates than step 1 did. See SUCCESS.md.
config = load_config(max_materialized=4, n_docks=2)
executor = CageFlow.build_executor(config)
budget = CageFlow.build_budget(config)

docks = CageFlow.dock_oligomer(config, executor, budget)
geometries = CageFlow.evaluate_geometry(docks, config, executor, budget, config["out_dir"])

selected = CageFlow.select_top_docks(docks, geometries, config, config["out_dir"])

n_passed = sum(1 for g in geometries if g["passed"])

check("select_top_docks returned a list of dock indices",
      isinstance(selected, list), str(type(selected)))
check(f"kept at most n_docks ({config['n_docks']}) candidates",
      len(selected) <= config["n_docks"], f"got {len(selected)}")
check("never selected more than the number that passed geometry",
      len(selected) <= n_passed, f"selected {len(selected)}, {n_passed} passed")
check("every selected index actually passed geometry",
      all(geometries[i]["passed"] for i in selected),
      str([geometries[i]["passed"] for i in selected]))

write_artifact(__file__, {
    "n_docks_materialized": len(docks),
    "n_passed_geometry": n_passed,
    "n_docks_config": config["n_docks"],
    "selected_indices": selected,
    "selected_scores": [docks[i]["solution"].get("score") for i in selected],
})

print(f"\nselected {len(selected)}/{n_passed} geometry-passing docks "
      f"(n_docks={config['n_docks']}): {selected}")
