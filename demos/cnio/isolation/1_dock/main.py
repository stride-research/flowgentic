"""Step 1: dock_oligomer.

See SUCCESS.md in this folder for what this proves, what it does not
touch, and how the input was reduced without reducing the science.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import check, load_config, write_artifact  # noqa: E402

import CageFlow  # noqa: E402

# max_materialized=1: the search grid is still fully scanned and scored
# (real science, unreduced); this only limits how many of the already-
# ranked candidates get the expensive step of building 3D coordinates.
# See SUCCESS.md for the full reasoning and the numbers that back it up.
config = load_config(max_materialized=1)
executor = CageFlow.build_executor(config)
budget = CageFlow.build_budget(config)

docks = CageFlow.dock_oligomer(config, executor, budget)

check("dock_oligomer returned exactly one candidate", len(docks) == 1, f"got {len(docks)}")

dock = docks[0]
solution = dock.get("solution", {})
assembly_pdb = dock.get("assembly_pdb", "")

check("candidate carries a solution with radius and spin",
      "radius" in solution and "spin" in solution,
      str(sorted(solution)))
check("candidate carries a non-empty assembly PDB",
      isinstance(assembly_pdb, str) and len(assembly_pdb) > 0,
      f"{len(assembly_pdb)} chars")

write_artifact(__file__, {
    "solution": solution,
    "n_copies": len(dock.get("copies", [])),
    "assembly_pdb_length": len(assembly_pdb),
    "assembly_pdb_preview": assembly_pdb[:500],
})

print(f"\n1/1 dock materialized: radius={solution.get('radius')} spin={solution.get('spin')}")
