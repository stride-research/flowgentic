"""Step 4: build_mpnn_config.

See SUCCESS.md in this folder for why this step is isolated on its own
even though Laura folds it into step 5's call path, and for what this
proves.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import check, load_config, write_artifact  # noqa: E402

import CageFlow  # noqa: E402

# Same reasoning as step 3: ranking needs more than one dock to be
# observable, and we need at least one SELECTED dock to hand to this
# step. max_materialized=4, n_docks=2 -- the grid is still unreduced.
config = load_config(max_materialized=4, n_docks=2)
executor = CageFlow.build_executor(config)
budget = CageFlow.build_budget(config)

docks = CageFlow.dock_oligomer(config, executor, budget)
geometries = CageFlow.evaluate_geometry(docks, config, executor, budget, config["out_dir"])
selected = CageFlow.select_top_docks(docks, geometries, config, config["out_dir"])

check("at least one dock was selected to build a config for",
      len(selected) > 0, f"got {len(selected)}")

i = selected[0]
dock = docks[i]
interface_resnum_sets = geometries[i]["interface_residues"]

out_dir = Path(config["out_dir"]) / f"dock_{i}"
mpnn_cfg, config_path = CageFlow.build_mpnn_config(dock, interface_resnum_sets, config, out_dir)

pdb_path = out_dir / "docked_assembly.pdb"

check("the PDB file for this dock actually exists on disk",
      pdb_path.exists(), str(pdb_path))
check("the PDB file is non-empty and matches the dock's own assembly text",
      pdb_path.read_text() == dock["assembly_pdb"],
      f"{pdb_path.stat().st_size if pdb_path.exists() else 0} bytes")
check("the ProteinMPNN config file was written",
      config_path.exists(), str(config_path))
written_cfg = json.loads(config_path.read_text())
check("the returned config dict and the written file agree",
      written_cfg == mpnn_cfg)
check("the config carries an output folder for ProteinMPNN to write into",
      "out_folder" in mpnn_cfg, str(sorted(mpnn_cfg)))

write_artifact(__file__, {
    "selected_dock_index": i,
    "pdb_path": str(pdb_path),
    "pdb_bytes": pdb_path.stat().st_size,
    "mpnn_config_path": str(config_path),
    "mpnn_config_keys": sorted(mpnn_cfg),
})

print(f"\nbuilt MPNN config + PDB for dock {i}: {pdb_path.stat().st_size} bytes, "
      f"config at {config_path}")
