# Step 3: `select_top_docks` — success criteria

## What this step is, in plain terms

Steps 1–2 produced some number of candidate placements and checked which ones are physically
valid. A real run can have far more valid candidates than it's worth spending compute on in the
next, much more expensive step (`design_interface`, ProteinMPNN). This step's only job is: rank
the geometry-passing candidates by quality, and keep only the best `config["n_docks"]` of them.
Everything not kept is still recorded (as `"not_selected"`), never silently dropped.

## Why this step needs MORE than one dock (unlike steps 1–2)

Ranking and trimming a list of exactly one item does nothing observable — you'd always "select"
the one thing you have. So steps 1–2's single-dock input doesn't exercise this step's actual
logic at all. We materialize 4 candidates instead of 1 and ask for the top 2
(`max_materialized=4, n_docks=2`), so there is something real for the ranking to discard.

**This is still not a reduced-science shortcut.** The docking search grid is scanned at the
exact same density regardless of how many candidates we ask to be materialized — see `1_dock`'s
SUCCESS.md for the evidence. We are only choosing a larger (but still small) number of
already-ranked candidates to keep, same mechanism as step 1, just a bigger number.

## What we are checking

- `select_top_docks()` returns a plain list of dock indices.
- It never keeps more than `config["n_docks"]`.
- It never "selects" more candidates than actually passed geometry (can't select what failed).
- Every index it did select genuinely passed geometry — no silent inclusion of a failed dock.

We are **not** re-deriving her ranking formula (`_dock_rank_key`, which sorts by the `select_by`
config key — `"cbeta"` in our probe config) to check she ranked "correctly" by our own
calculation. That would mean reimplementing her scoring logic ourselves, which is exactly the
kind of fake science this harness exists to avoid. We check the *contract* (never over the
limit, never includes a failure, always a valid list), not whether we'd have picked the same
order.

## What we are NOT touching

- `CageFlow.select_top_docks()` — the real function, called with real dock and geometry results.
- Its internal ranking (`_dock_rank_key`) and its bookkeeping of non-selected docks into history
  (`append_history`, `_dock_outcome`) — both hers, untouched, called as-is.
- `dock_oligomer()` and `evaluate_geometry()` — regenerated fresh here, same real calls as steps
  1 and 2, chained because both are cheap.

## Expected result (actual numbers from the last real run)

```
  [PASS] select_top_docks returned a list of dock indices  -- <class 'list'>
  [PASS] kept at most n_docks (2) candidates  -- got 2
  [PASS] never selected more than the number that passed geometry  -- selected 2, 4 passed
  [PASS] every selected index actually passed geometry  -- [True, True]

selected 2/4 geometry-passing docks (n_docks=2): [1, 3]
```

All 4 materialized candidates passed geometry this run (not guaranteed every time — the grid
point values differ run to run only if the config or scaffold changes, and neither has here), so
this is a genuine case of the ranking actually discarding 2 of 4 valid candidates, not an
edge case where everything trivially passes through.

See `artifact.json` in this folder for the full result from the last cluster run.
