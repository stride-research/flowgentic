# Step 1: `dock_oligomer` — success criteria

## What this step is, in plain terms

The input is one protein shape repeated 3 times (`1c5e_trimer.pdb`, a symmetric trimer).
Docking tries placing those 3 copies at a point on a search grid (a radius and a rotation
angle) and checks whether they come together into a closed, physically sensible cage shape.
Each grid point is one "candidate." A real run tries hundreds or thousands of grid points and
keeps the best few.

## What we are checking

That `dock_oligomer()` — called for real, with the real trimer — can build at least one
candidate correctly: a solution with a radius and a spin angle, and a non-empty 3D structure
(a PDB file) assembled from it. That's it. We are not checking that this *particular* candidate
is scientifically good (no geometry filter has run yet — that's step 2's job).

## What we are NOT touching

Everything that actually does the work is Laura's unmodified code, called with real arguments:

- `CageFlow.dock_oligomer()` — the real batch function, not a copy or a reimplementation.
- Inside it: the real symmetry math (`get_symmetry_matrices`, `get_canonical_axis`,
  `get_coset_reps`, `align_axes` from ProteinBuilder) and the real grid search
  (`scan_docking`). We do not compute any of this ourselves.
- `_materialize_dock()`, the per-candidate worker that actually builds the 3D structure, is
  called internally by her own dispatch path (`_charge` → `executor.map_timed`), exactly as a
  real run calls it. We never call it directly with hand-built fake arguments.

If this step passes, it is evidence about *her* code, not about anything we wrote.

## Which config, and why — this matters, so it's explicit

**This is not the config Laura told us to use.** Her Oct 2 push (commit `b24916b`, which matches
her message about the trimer word-for-word) touched exactly two files: `1c5e_trimer.pdb` and
`configs/config.1c5e_noflip.json`. That full-scale config is the literal one she pointed to —
and it is expensive (`num_seq_per_target: 100`, `fold_oligomers: true`).

We use `config.1c5e_probe.json` instead: a different, pre-existing, lighter sibling already in
her repo, chosen by us specifically for isolation. Verified before using it, not assumed: its
`dock_ranges` scan the grid at `radius_step: 0.25`, *finer* than noflip's `0.5` — so the actual
docking search is not reduced at all, if anything denser. The only things probe makes cheaper
are the later, expensive stages (design, fold), which is exactly what a per-step isolation check
wants and the full-scale config doesn't need to pay for yet.

`_shared.load_config(base=...)` can load either by name; probe is the default for isolation,
`config.1c5e_noflip.json` is what the eventual full reference run should use instead.

## How we shrank THIS step's input without shrinking the science

On top of that base config, we used `config["max_materialized"] = 1`, a real, already-existing
production setting of hers — not a fake grid or a cheaper version of the symmetry math.
`max_materialized` only limits how many of the (identically-computed, identically-ranked)
candidates get the expensive step of actually building 3D coordinates. The full grid is still
searched; every candidate is still scored; we just stop before building all of them, because our
question — "does this work at all" — only needs one.

**If a future change to this script ever reduces the grid itself, shortens the symmetry
search, or substitutes a fake/simplified version of any of the real functions above, that is a
red flag and should be treated as a broken isolation test, not a faster one.**

## Expected result

```
  [PASS] dock_oligomer returned exactly one candidate  -- got 1
  [PASS] candidate carries a solution with radius and spin  -- [...]
  [PASS] candidate carries a non-empty assembly PDB  -- N chars

1/1 dock materialized: radius=<value> spin=<value>
```

See `artifact.json` in this folder for what actually came back the last time this ran on the
cluster.
