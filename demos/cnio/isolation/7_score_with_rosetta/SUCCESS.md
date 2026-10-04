# Step 7: `score_with_rosetta` (real PyRosetta) — success criteria

## What this step is, in plain terms

Step 5's design score is a neural network's opinion of how "plausible" a sequence looks. This
step is the real physics check: thread the designed sequence onto the actual docked structure,
repack the side chains into physically realistic positions, and compute the binding energy
(`dG_separated`) using Rosetta's force field. This is **the only pass/fail gate in the entire
pipeline** — everything upstream only filters or records.

## A real bug this found, not an environment issue

The first attempt crashed with `TypeError: unsupported operand type(s) for -: 'str' and 'int'`
inside `thread_sequence()`. The real cause: that function does `for resnum in design_positions:`,
expecting a **flat list** of residue numbers reused for every chain. We had passed it the
`{chain: [residue numbers]}` **dict** straight from `mpnn_input.json` — the shape ProteinMPNN's
own input format uses, a different consumer with a different contract. Iterating a dict in
Python walks its keys (the chain letters, e.g. `"A"`), not its values, which is exactly how a
string ended up where an int was expected.

Verified before fixing, not assumed: every chain's list in the real `mpnn_input.json` is
identical (confirmed by direct comparison), which is exactly what a homooligomeric tie means —
one shared interface, not a per-chain difference — so using chain A's list alone loses nothing.

## Why this step runs differently from every other step

This is the one script that cannot share `_shared.py` or import `CageFlow.py` at all. Checked
directly: `CageFlow.py` imports `history_table.py` at module load, which imports `pandas`; and
even `numpy` alone fails in this specific venv (`~/venvs/cageflow`) with a CPU instruction-set
mismatch (`RuntimeError: NumPy was built with baseline optimizations... but your machine doesn't
support`). Neither pandas nor a working numpy is needed for Rosetta scoring itself — confirmed by
checking `rosetta_scoring.py`'s own imports (stdlib + pyrosetta + two sibling modules, nothing
else) and that it imports cleanly standalone under this venv. So this script imports
`rosetta_scoring` directly and calls `rosetta_scoring.score_sequence()` — the exact function
`CageFlow._score_one_sequence_rosetta()` calls; that wrapper adds nothing but a try/except.

## What we are checking

- `score_sequence()` returns the real, full metric dict (every key in `METRIC_KEYS`, not just the
  headline number).
- `dG_separated` (the ddG-style binding energy the real pipeline sorts on) is a real float.
- `shape_complementarity` (a real geometric packing-quality metric) is a real float.

## What we are NOT touching

- `rosetta_scoring.score_sequence()` and everything it calls internally (`thread_sequence`,
  `apply_symmetric_packing`, `compute_sasa`, `compute_interface_metrics`,
  `symmetric_interaction_energy`, `pair_interface_metrics`) — all real, unmodified, called on real
  data.

## A known, named gap — not hidden, not yet closeable

**Per-dock sequence dedup is not exercised by this test**, and that is a real, structural
limitation worth being explicit about rather than papering over. That dedup bookkeeping
(`seen_seq`/`duplicate_of`) lives inside `CageFlow.score_with_rosetta()` — the **batch** function,
not the worker — and that function is in `CageFlow.py`, which cannot be imported in this venv at
all (see above). So neither this step nor any other can currently test it. The fix is
straightforward in principle (install a working numpy + pandas into `~/venvs/cageflow`) but is a
real environment change, not something to route around quietly. Tracked here so it isn't lost:
**fixing that venv would unlock testing the real batch `score_with_rosetta()`, including dedup,
directly** — a strictly better test than anything possible right now.

**There is also no standalone `dedup_sequences` function anywhere in CageFlow** — confirmed by
grep across the whole repo. Dedup is inline, twice, in two different places with two different
scopes: per-dock here (not yet testable, see above), and globally inside `fold_oligomer()` (next
step). There is no step 6 folder for this reason — a numbered folder for a function that doesn't
exist would be exactly the kind of manufactured step we retired `4_mpnn_config` for.

## Expected result (actual numbers from the last real run)

```
  [PASS] score_sequence returned the real metric set  -- [...]
  [PASS] dG_separated is a real number  -- 142.20147455118376
  [PASS] shape_complementarity is a real number  -- 0.612301230430603

dG_separated = 142.201  shape_complementarity = 0.612
```

This dG is markedly less favorable than dock 44's recorded aggregate (`group_ddg = -0.402` across
her real 100 sequences). That is not evidence of a bug: `fold_seq_20`'s specific sequence is one
of 100 real designs, individual sequences vary widely (Laura's own measured finding: an 11×
spread in Rosetta's run-to-run noise between sequences), and we have no way to confirm which
specific index in her recorded `per_seq_ddg` array this exact sequence corresponds to. The
honest claim is narrower and still real: a genuine sequence, threaded onto a genuine structure,
scored by genuine unmodified PyRosetta code, produced a genuine number.

See `artifact.json` in this folder for the full real metric set from the last cluster run.
