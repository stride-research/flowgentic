"""Step 1b: dock_two_component (two-component docking).

Unrelated to the 1c5e trimer campaign (that is a one-component case), but
`dock_two_component` is a real, separate function in CageFlow and gets
isolated too -- skipping a real code path because our current target
doesn't exercise it is exactly the kind of scope-narrowing this harness
exists to avoid.

There is no production config for two-component docking in configs/ --
it isn't part of this campaign. Laura's own tests/test_dock_two_component.py
is the validated source of truth instead: real protein-cage-designer, a
real (deliberately coarse) scan, two real fixture structures
(4iyq.pdb1 + 5dii.pdb1 under architecture T33). We use her exact CONFIG,
repointing only the paths to our cluster clones.

See SUCCESS.md in this folder for what this proves and what it does not
touch.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from _shared import CAGE_DESIGNER_PATH, CAGEFLOW_DIR, check, write_artifact  # noqa: E402

import CageFlow  # noqa: E402
from records import component_chains, dock_mode, is_two_component  # noqa: E402

FIXTURES = CAGEFLOW_DIR / "tests" / "fixtures"

# Laura's own tests/test_dock_two_component.py CONFIG, verbatim apart from
# paths. The coarse spin_step (40 degrees) is HER choice for keeping this
# fast (~9 pairs after symmetry collapses the sweep), not a reduction we
# introduced.
config = {
    "scaffold_pdb": str(FIXTURES / "4iyq.pdb1"),
    "scaffold_pdb_b": str(FIXTURES / "5dii.pdb1"),
    "architecture": "T33",
    "cage_designer_path": CAGE_DESIGNER_PATH,
    "dock_ranges": {
        "radius_a": [25.0, 50.0], "radius_step_a": 5.0, "spin_step_a": 40.0,
        "radius_b": [35.0, 60.0], "radius_step_b": 5.0, "spin_step_b": 40.0,
    },
    "n_docks": 2,
    "clash_dist": 3.0, "contact_dist": 12.0,
    "cross_clash_dist": 3.0, "cross_contact_dist": 10.0, "min_contacts": 3,
    "executor": "sequential",
    "out_dir": str(Path(__file__).resolve().parent / "_scratch"),
}

executor = CageFlow.build_executor(config)
budget = CageFlow.build_budget(config)

docks = CageFlow.dock_two_component(config, executor, budget)

check("dock_two_component returned at least one candidate", len(docks) > 0, f"got {len(docks)}")

dock = docks[0]

check("dock_mode reports two_component", dock_mode(dock) == "two_component", dock_mode(dock))
check("is_two_component sees the whole run as two-component",
      is_two_component(docks), str(is_two_component(docks)))
check("merged assembly is copies_a then copies_b, in that order",
      dock["copies"] == dock["copies_a"] + dock["copies_b"])

chains_a, chains_b = component_chains(dock)
check("both components claim assembly chains",
      bool(chains_a) and bool(chains_b), f"a={len(chains_a)} b={len(chains_b)}")
check("no chain letter belongs to both components",
      not (set(chains_a) & set(chains_b)), str(set(chains_a) & set(chains_b)))

write_artifact(__file__, {
    "n_docks": len(docks),
    "chains_a": len(chains_a),
    "chains_b": len(chains_b),
    "assembly_pdb_length": len(dock.get("assembly_pdb", "")),
})

print(f"\n{len(docks)} two-component dock(s) materialized: "
      f"A={len(chains_a)} chains, B={len(chains_b)} chains")
