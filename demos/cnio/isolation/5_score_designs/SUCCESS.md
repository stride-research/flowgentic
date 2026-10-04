# Step 5: `score_designs` — success criteria

## What this step is, in plain terms

Step 4 gave each selected dock several candidate sequences, each with ProteinMPNN's own
per-sequence confidence score. This step does the group-level rollup: for each dock, take the
mean of the best `config["top_fraction_for_score"]` (75% by default) of its sequence scores, one
number summarizing "how good are this dock's designs overall."

**This number gates nothing.** It's recorded to history and is informational only — the real
pass/fail gate is Rosetta's ddG (step 7).

## Why it is slow

This chains the full `dock → geometry → select → design` pipeline from scratch rather than
reusing another step's output, so it is self-contained — but that means it pays ProteinMPNN's
real ~90-second cost for *both* selected docks again, on top of whatever score_designs itself
adds (negligible — it's pure arithmetic over numbers `design_interface` already produced). Steps
7 onward switch to Laura's own committed real fixture instead, specifically to stop paying this
cost repeatedly — see step 7's SUCCESS.md.

## What we are checking

**For every scored dock**, not just one (same reasoning as step 4 — docks have genuinely
different interface sizes, so one dock passing doesn't establish the others did):

- `score_designs()` returns a dict keyed only by docks that were actually designed.
- Every designed dock got scored — no silent gaps.
- `per_seq_scores` has exactly one entry per sequence `design_interface` produced for that dock.
- `group_score` is a real number, and — a structural sanity check, not a re-derivation of her
  formula — falls within the range of that dock's own per-sequence scores. A mean over *some*
  subset of a list cannot fall outside that list's min/max; we don't check it equals our own
  recomputed mean, since that would mean reimplementing `records.group_score()` ourselves.

## What we are NOT touching

- `CageFlow.score_designs()` — the real function, called on real `design_interface()` output.
- `records.group_score()` (imported as `_group_score`) — her real aggregation formula, not
  reimplemented or checked against our own calculation.
- `dock_oligomer()`, `evaluate_geometry()`, `select_top_docks()`, `design_interface()` —
  regenerated fresh here, same real calls as steps 1–4.

## Expected result (actual numbers from the last real run)

```
  [PASS] at least one dock has designs to score  -- got 2
  [PASS] score_designs returned a dict keyed only by docks that were designed  -- keys=[1, 3], designed=[1, 3]
  [PASS] every designed dock got scored  -- scored=[1, 3], designed=[1, 3]
  [PASS] dock 1: per_seq_scores has one entry per designed sequence  -- 4 vs 4 designs
  [PASS] dock 1: group_score is a real number
  [PASS] dock 1: group_score falls within the per-sequence score range  -- group=0.6804, range=[0.616, 0.7823]
  [PASS] dock 3: per_seq_scores has one entry per designed sequence  -- 4 vs 4 designs
  [PASS] dock 3: group_score is a real number
  [PASS] dock 3: group_score falls within the per-sequence score range  -- group=0.5910, range=[0.5521, 0.6906]

dock 1: group_score=0.6804 over 4 sequences [0.616, 0.6997, 0.7823, 0.7256]
dock 3: group_score=0.5910 over 4 sequences [0.5521, 0.5986, 0.6906, 0.6224]
```

See `artifact.json` in this folder for the full result from the last cluster run.
