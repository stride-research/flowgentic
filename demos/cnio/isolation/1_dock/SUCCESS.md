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

## How we shrank the input without shrinking the science

We did **not** write a smaller fake protein, a smaller fake grid, or a cheaper fake version of
the symmetry math. We used `config["max_materialized"] = 1`, a real, already-existing
production setting of hers.

Checked directly against her own numbers before using this: her small sanity config
(`config.1c5e_probe.json`, which we build on) scans the search grid at `radius_step: 0.25`,
*finer* than her full-scale config's `0.5` — so the search itself is not reduced at all, if
anything it's denser. `max_materialized` only limits how many of the (identically-computed,
identically-ranked) candidates get the expensive step of actually building 3D coordinates for.
The full grid is still searched; every candidate is still scored; we just stop before building
all of them, because our question — "does this work at all" — only needs one.

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
