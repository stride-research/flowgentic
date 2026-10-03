# Step 1b: `dock_two_component` (two-component) — success criteria

## What this step is, in plain terms

Step 1a docks a cage made of **one** protein type repeated several times. Some cages are
instead built from **two different** protein types that fit together — think two different
puzzle pieces instead of one piece repeated. `dock_two_component` is the real docking search
for that case: it scans positions for component A and component B separately, then pairs them
up and checks which pairings close into a sensible joint cage.

**This has nothing to do with the 1c5e trimer we're actually validating** — that's a
one-component case, handled entirely by step 1a. We isolate this anyway because it's a real,
separate function in CageFlow, and skipping a real code path because our current target doesn't
exercise it is exactly the kind of scope-narrowing this harness exists to avoid.

## What we are checking

That `dock_two_component()`, called for real on two real (unrelated) protein structures, builds
at least one candidate where: both components are present and labeled correctly, the merged
structure is built in the right order (A's copies, then B's), and no chain letter is claimed by
both components at once (which would make it impossible to tell which atoms belong to which
protein downstream).

## What we are NOT touching

- `CageFlow.dock_two_component()` — the real function, not a copy.
- Inside it: the real per-component placement math and `_materialize_dock_2c`, the real worker,
  called through her own dispatch path exactly as a real run would.
- `records.py`'s `dock_mode`, `is_two_component`, `component_chains` — her own accessor
  functions for reading a two-component result, used here exactly as her own test uses them,
  not reimplemented.

## Which config, and why

**There is no production config for this in `configs/`** — two-component docking isn't part of
the 1c5e campaign, so Laura never needed one for it there. Instead of inventing one ourselves
(which would mean guessing at reasonable scientific values), we use her own validated source:
`tests/test_dock_two_component.py`'s `CONFIG` dict, copied verbatim except for paths. Its two
real fixture structures (`4iyq.pdb1`, `5dii.pdb1`, the same pairing her archived example config
used, per her own test's docstring) ship with her repo and are confirmed present on the cluster.

The grid here is coarse (40° spin steps) by **her** choice, documented in her own test's
docstring as enough to exercise the code without being a benchmark — not a reduction we
introduced for this isolation script.

## Expected result (actual numbers from the last real run)

```
[2c prepare] component A self-sweep: 3 valid spins in 0.0s
[2c prepare] component B self-sweep: 3 valid spins in 0.0s
[2c prepare] precompute done in 0.0s -- 3 x 3 = 9 candidate pairs ahead in Stage 4
[2c chunk None] finished: 9 pairs in 2.4s (3.80 pairs/s), 9 solutions kept
  [PASS] dock_two_component returned at least one candidate  -- got 9
  [PASS] dock_mode reports two_component  -- two_component
  [PASS] is_two_component sees the whole run as two-component  -- True
  [PASS] merged assembly is copies_a then copies_b, in that order
  [PASS] both components claim assembly chains  -- a=12 b=12
  [PASS] no chain letter belongs to both components  -- set()

9 two-component dock(s) materialized: A=12 chains, B=12 chains
```

See `artifact.json` in this folder for the full result from the last cluster run.
