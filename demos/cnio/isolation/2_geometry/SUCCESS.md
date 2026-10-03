# Step 2: `evaluate_geometry` — success criteria

## What this step is, in plain terms

Step 1 produced one candidate: 3 copies of the protein placed at a specific radius and
rotation. That placement might still be physically nonsensical — the copies could overlap
(a "clash"), or barely touch at all (too few "contacts" to hold together as a cage). Geometry
runs an independent, more careful check of exactly that: real clash detection, real contact
counting, and a stricter side-chain-level check called "cbeta." It is the first real pass/fail
gate in the whole pipeline.

**Important distinction, directly from her own code comment:** the `contacts`/`min_clash`
numbers step 1's artifact already showed you came from the *coarse* scoring `scan_docking` uses
internally, purely to rank the search. This step recomputes clashes and contacts from scratch,
properly — her docstring calls it explicitly "an independent, authoritative clash/contact
check, divorced from dock_oligomer." The two numbers are not expected to agree, and we do not
check that they do.

## What we are checking

That `evaluate_geometry()`, called for real on a real dock, returns one result carrying a
boolean pass/fail and two non-negative counts (clashes, contacts). We are **not** checking
*which* way it decided (pass or fail) — a real geometric outcome, whichever it is, is itself
the thing we're validating works. We're also not re-deriving her pass/fail formula ourselves to
check she got it "right" — that would mean reimplementing her logic, which is exactly the kind
of fake science-on-our-side this harness exists to avoid. The formula is hers; we check the
shape of what it returns, not whether we'd compute it the same way.

## What we are NOT touching

- `CageFlow.evaluate_geometry()` — the real batch function, called with the real dock list.
- Inside it: the real `_evaluate_one_geometry()` worker, the real `_steric_gate` check, and the
  real `src.interface` contact-counting code from ProteinBuilder. None of this is reimplemented
  or approximated by us.
- `CageFlow.dock_oligomer()` — regenerated fresh here (see below), same real call as step 1.

## How we shrank the input without shrinking the science

Two things, both using her own real mechanisms, nothing invented:

1. `max_materialized=1` (as in step 1) — the search grid is still fully scanned and scored;
   we just materialize one candidate to feed geometry, since our question only needs one.
2. We regenerate that one dock by calling `dock_oligomer()` again, fresh, rather than reusing
   step 1's saved artifact — because step 1's `artifact.json` only kept a small summary (for
   readability), not the full in-memory dock dict `evaluate_geometry` actually needs (the
   per-copy structure data). Re-running `dock_oligomer()` is cheap (sub-second to a few seconds)
   and is the real call either way, so there's no fidelity lost by not caching it.

**If a future change to this script ever simplifies, skips, or approximates the real clash/
contact/cbeta checks instead of calling her code for them, that is a red flag, not a speedup.**

## Expected result

```
  [PASS] evaluate_geometry returned one result for our one dock  -- got 1
  [PASS] result carries a boolean pass/fail  -- True (or False -- either is a valid real result)
  [PASS] result carries a non-negative clash count  -- N
  [PASS] result carries a non-negative contact count  -- N

geometry: passed=<bool> n_clashes=<int> n_contacts=<int>
```

See `artifact.json` in this folder for what actually came back the last time this ran on the
cluster.
