"""Step 1: dock_oligomer -- the real docking search, down to ONE candidate.

Calls CageFlow's own batch function `dock_oligomer()`, not a hand-built
fake input to `_materialize_dock`. Her setup math (canonical_axis,
coset_reps, R_align, com -- symmetry and axis alignment) is nontrivial
and already correct in her code; reimplementing a fake version of it
here would test our fake math, not hers.

`max_materialized=1` makes her own code stop at exactly one real
candidate, built by the real `_materialize_dock` worker, through the
real dispatch path (`_charge` -> `executor.map_timed`). This is as
isolated as the real pipeline allows without inventing inputs.

    srun -A stride -p cpu -c 4 --mem=8G -t 00:10:00 python 1_dock.py
"""

from _shared import check, load_config, write_artifact

import CageFlow

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

write_artifact("1_dock", {
    "solution": solution,
    "n_copies": len(dock.get("copies", [])),
    "assembly_pdb_length": len(assembly_pdb),
    "assembly_pdb_preview": assembly_pdb[:500],
})

print(f"\n1/1 dock materialized: radius={solution.get('radius')} spin={solution.get('spin')}")
