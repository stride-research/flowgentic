"""Step 2: evaluate_geometry.

See SUCCESS.md in this folder for what this proves, what it does not
touch, and how the input was reduced without reducing the science.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import check, load_config, write_artifact  # noqa: E402

import CageFlow  # noqa: E402

# max_materialized=1: same reasoning as step 1 -- we only need one real
# dock to feed geometry, the search itself is unreduced. See 1_dock's
# SUCCESS.md for the full argument.
config = load_config(max_materialized=1)
executor = CageFlow.build_executor(config)
budget = CageFlow.build_budget(config)

# Real dock, generated fresh -- cheap (sub-second), so chaining the real
# call is simpler and more faithful than hand-storing step 1's raw output.
docks = CageFlow.dock_oligomer(config, executor, budget)

geometries = CageFlow.evaluate_geometry(docks, config, executor, budget, config["out_dir"])

check("evaluate_geometry returned one result for our one dock",
      len(geometries) == 1, f"got {len(geometries)}")

geometry = geometries[0]

check("result carries a boolean pass/fail",
      isinstance(geometry.get("passed"), bool), str(geometry.get("passed")))
check("result carries a non-negative clash count",
      isinstance(geometry.get("n_clashes"), int) and geometry["n_clashes"] >= 0,
      str(geometry.get("n_clashes")))
check("result carries a non-negative contact count",
      isinstance(geometry.get("n_contacts"), int) and geometry["n_contacts"] >= 0,
      str(geometry.get("n_contacts")))

write_artifact(__file__, {
    "passed": geometry["passed"],
    "n_clashes": geometry["n_clashes"],
    "n_contacts": geometry["n_contacts"],
    "steric_ok": (geometry.get("steric") or {}).get("ok"),
    "dock_solution": docks[0].get("solution", {}),
})

print(f"\ngeometry: passed={geometry['passed']} "
      f"n_clashes={geometry['n_clashes']} n_contacts={geometry['n_contacts']}")
