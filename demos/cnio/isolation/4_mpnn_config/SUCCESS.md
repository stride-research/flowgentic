# Step 4: `build_mpnn_config` — success criteria

## Why this folder exists at all

Laura's own operation map calls this "not a separate step" — `build_mpnn_config()` runs *inside*
`design_interface()` (step 5), not as its own pipeline stage, and she said to drop its row from
the map. But it is a real, separate, independently-callable function in her code (her own
docstring even says it's *"used both directly, e.g. from a notebook"*), and skipping a real
function because production doesn't call it as a top-level stage is exactly the kind of
scope-narrowing this harness exists to forbid. So it gets a folder, numbered 4, same as every
other real function — even though the production pipeline folds it into step 5's call.

## What this step is, in plain terms

Once a dock is selected (step 3), something has to actually write that dock's 3D structure to a
file and build the configuration ProteinMPNN needs to design sequences for it. That's this
function: given one selected dock and its interface residues (from step 2's geometry check), it
writes the dock's structure as a real PDB file and a JSON config describing which residues
ProteinMPNN should redesign.

## What we are checking

This is also where **step 3's criterion actually gets fulfilled** — see step 3's SUCCESS.md for
why it couldn't be checked there. Specifically:

- A real `.pdb` file is written to disk for the selected dock (not just held in memory).
- Its contents match the dock's own structure exactly — nothing is altered in transit.
- A ProteinMPNN config file is written alongside it, and it's non-empty.
- The function's return value (what the real pipeline actually uses) carries the fields
  `design_interface` needs, e.g. an output folder for ProteinMPNN's results.

## What we are NOT touching

- `CageFlow.build_mpnn_config()` — the real function, called with real inputs produced by real
  calls to steps 1–3 (chained fresh here, same reasoning as step 3 — all three are cheap).
- The real residue-numbering conversion it does internally (`_resnums_to_mpnn_positions`) — not
  reimplemented or checked against our own calculation, only checked for presence/consistency.

## Expected result

```
  [PASS] at least one dock was selected to build a config for  -- got 2
  [PASS] the PDB file for this dock actually exists on disk  -- <path>
  [PASS] the PDB file is non-empty and matches the dock's own assembly text  -- N bytes
  [PASS] the ProteinMPNN config file was written  -- <path>
  [PASS] the returned config dict and the written file agree  -- 
  [PASS] the config carries an output folder for ProteinMPNN to write into  -- [...]

built MPNN config + PDB for dock <i>: N bytes, config at <path>
```

See `artifact.json` in this folder for the full result from the last cluster run.
