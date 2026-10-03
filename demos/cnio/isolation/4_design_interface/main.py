"""Step 4: design_interface (ProteinMPNN).

See SUCCESS.md in this folder for what this proves, what it does not
touch, and why the retired mpnn_config step's PDB check lives here now.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _shared import check, load_config, write_artifact  # noqa: E402

import CageFlow  # noqa: E402

# Same reasoning as step 3: need more than one dock so there's something
# real to select and design. max_materialized=4, n_docks=2 -- the search
# grid is still unreduced, see 1_dock's SUCCESS.md.
config = load_config(max_materialized=4, n_docks=2)
executor = CageFlow.build_executor(config)
budget = CageFlow.build_budget(config)

docks = CageFlow.dock_oligomer(config, executor, budget)
geometries = CageFlow.evaluate_geometry(docks, config, executor, budget, config["out_dir"])
selected = CageFlow.select_top_docks(docks, geometries, config, config["out_dir"])

check("at least one dock was seljected to design", len(selected) > 0, f"got {len(selected)}")

# The real top-level call. Internally this calls build_mpnn_config() for
# each selected dock, which writes the PDB + MPNN config files to disk --
# we check those as a SIDE EFFECT below, not by calling that helper
# ourselves. See SUCCESS.md for why this folder absorbed that check.
designed = CageFlow.design_interface(
    docks, geometries, selected, config, executor, budget,
    config["out_dir"], config["mpnn_dir"])

check("design_interface returned a dict keyed by selected dock indices",
      isinstance(designed, dict) and set(designed) <= set(selected),
      f"keys={sorted(designed)}, selected={selected}")
check("at least one dock designed successfully",
      len(designed) > 0, f"got {len(designed)} of {len(selected)} selected")

i = next(iter(designed))
sequences = designed[i]
n_expected = config["num_seq_per_target"]

check(f"designed the expected number of sequences ({n_expected})",
      len(sequences) == n_expected, f"got {len(sequences)}")
check("every design carries a non-empty sequence and a real score",
      all(d.get("seq") and isinstance(d.get("score"), float) for d in sequences),
      str(sequences[0]) if sequences else "no designs")

# The retired standalone mpnn_config step's check, now verified as a side
# effect of the real call above instead of calling build_mpnn_config
# ourselves.
pdb_path = Path(config["out_dir"]) / f"dock_{i}" / "docked_assembly.pdb"
mpnn_cfg_path = Path(config["out_dir"]) / f"dock_{i}" / "mpnn_input.json"
check("the PDB file for the designed dock was written to disk",
      pdb_path.exists() and pdb_path.read_text() == docks[i]["assembly_pdb"],
      str(pdb_path))
check("the ProteinMPNN config file was written to disk",
      mpnn_cfg_path.exists(), str(mpnn_cfg_path))

write_artifact(__file__, {
    "designed_dock_index": i,
    "n_sequences": len(sequences),
    "sequences": [{"seq": d["seq"], "score": d["score"]} for d in sequences],
    "pdb_path": str(pdb_path),
    "mpnn_config_path": str(mpnn_cfg_path),
})

print(f"\ndesigned {len(sequences)} sequences for dock {i}, "
      f"scores: {[round(d['score'], 3) for d in sequences]}")
