# Step 7: `fold_oligomer` (real ColabFold, MSA fetch + structure prediction) — success criteria

## What this step is, in plain terms

Step 6 checked the designed sequence's binding energy. This step checks the structural
question: does the designed sequence actually fold into the shape it was designed for? It fetches
an evolutionary alignment (MSA) for the sequence, then runs ColabFold's AlphaFold2-multimer model
to predict a 3D structure, and compares that structure to the real docked pose. Like step 6, this
is **record-only** — nothing downstream reads fold's output to pass/fail a design; it's evidence
a scientist reviews. This step calls `CageFlow.fold_oligomer()` directly — the one real top-level
function the pipeline itself calls for folding.

Plain-English glossary for the four numbers in the artifact:
- `mean_plddt` (0-100): the model's own per-residue confidence in its predicted structure,
  averaged. Above ~90 is "very confident," below ~50 is "don't trust this region."
- `ptm` / `iptm` (0-1): confidence in the overall fold / specifically in how the chains interface
  with each other. Higher is better; above ~0.8 is considered a strong complex prediction.
- `rmsd`: how far the predicted structure's backbone deviates (in Angstrom) from the real docked
  pose it was designed against. Lower is better; this is a geometric sanity check, not a
  confidence score.

## Why this is one step, not two (7a/7b)

An earlier version of this folder split into `8a_fold_msa` and `8b_fold_inference`, each calling
one of `fold_oligomer()`'s two internal workers (`_fetch_one_msa`, `_fold_one_sequence`)
separately. That split exists in the real pipeline for a real reason — Laura measured the MSA
fetch idling a GPU for ~40s on a real run, so production dispatches the fetch to a `"cpu"` worker
pool and the inference to a `"gpu"` pool, via `config["executor"]`'s parallel backend. But every
isolation script here uses `config["executor"] = "sequential"` (a plain for-loop, see
`_shared.py`), and `_charge()`'s `"cpu"`/`"gpu"` strings are purely budget-tracking labels —
`executor.map_timed()` runs the exact same way regardless (confirmed by reading `budget.py` and
`CageFlow._charge()`). So under `"sequential"`, there is no actual resource split to test
separately: calling `fold_oligomer()` once already runs the MSA fetch then the inference, in
order, in one process — exactly the real sequencing, just without the parallel optimization that
only matters for a real multi-worker executor. Splitting it into two isolation scripts didn't
test anything the real pipeline does differently; it just mirrored an internal implementation
detail that isn't externally observable under this executor. One real call to the one real
top-level function is both simpler and a more accurate test of what actually ships.

## What we are checking

- `fold_oligomer()` returns results for at least one dock (confirms `fold_oligomers=True` was
  actually honored, not silently skipped — see its own docstring: "OPT-IN: does nothing unless
  config['fold_oligomers'] is true").
- Exactly `fold_top_n` sequences were folded per dock (the real ranking/selection logic inside
  `fold_oligomer()`, not something we pick).
- For each folded (dock, sequence): the fold reported `ok: True`, a real `.pdb` file exists on
  disk, `mean_plddt` is a real float, and `rmsd` is either a real float or a named failure reason.

## What we are NOT touching

- `fold_oligomer()`, `_fetch_one_msa()`, `_fold_one_sequence()`, `_rank_sequences_for_fold()` — all
  real, unmodified. `fold_sequence()`/`colabfold_batch` run the actual AlphaFold2-multimer model.

## Inputs: fully chained, not a fixture

Unlike the first version of this step (and of step 6), this script chains
`dock_oligomer -> evaluate_geometry -> select_top_docks -> design_interface -> score_designs ->
score_with_rosetta -> fold_oligomer` for real, same `n_docks=1, num_seq_per_target=1` reasoning as
step 6 (multi-dock/multi-sequence iteration already proven in steps 3-5; this step is about
proving fold works end to end on genuinely fresh output, not re-proving dock/design selection).
`native_pdb_text` is sourced exactly as `fold_oligomer()` sources it internally
(`docked_oligomer["copies"][0]`) — not something this script picks.

## An environment bug this found, twice (fixed, not CageFlow's)

Same root cause as the old step 8a/8b found: `module load PyTorch` sets `PYTHONPATH` to EESSI's
own `python3.11` site-packages. `fold_oligomer()` spawns ColabFold's self-contained `python3.10`
and `colabfold_batch` as subprocesses, which inherit `os.environ` wholesale and would otherwise
pick up the EESSI module's numpy (wrong Python ABI) instead of their own bundled one.

The first version of this script popped `PYTHONPATH` right after importing `CageFlow`, at the top
of the file -- reasoning that this process's own imports were already cached by then, so it no
longer needed `PYTHONPATH` for anything of its own. That reasoning missed something: it's not
just THIS process's imports that depend on `PYTHONPATH`. `design_interface()` (called well before
`fold_oligomer()`) spawns ProteinMPNN's helper scripts via `sys.executable` -- the SAME unified
venv's own python, which has no numpy of its own at all; it only resolves numpy via the
module-injected `PYTHONPATH` (confirmed directly: step 6, which never touches fold and never pops
`PYTHONPATH`, passes cleanly). Popping it at the top broke that earlier, unrelated subprocess call
with the exact same `ModuleNotFoundError: No module named 'numpy'` this fix was meant to prevent
elsewhere -- caught by actually running it, not by re-reading the code. Fixed by moving the pop to
immediately before the `fold_oligomer()` call, after every earlier real call that still needs
`PYTHONPATH` intact. Nothing in `CageFlow.py`, `run_mpnn.py`, `fold.py`, or `colabfold_msa.py` was
touched.

## Expected result (actual numbers from the last real run)

```
  [PASS] at least one dock has Rosetta scores to rank fold candidates by  -- got 1
  [PASS] fold_oligomer returned results for at least one dock  -- got 1 dock(s)
  [PASS] dock 0: fold_top_n=1 sequence(s) were folded  -- got 1
  [PASS] dock 0 seq 0: fold reported ok  -- None
  [PASS] dock 0 seq 0: a real PDB was written  -- .../dock_0/fold_seq_0/msa_unrelaxed_rank_001_alphafold2_multimer_v3_model_1_seed_20260831.pdb
  [PASS] dock 0 seq 0: mean_plddt is a real number  -- 97.1702807017544
  [PASS] dock 0 seq 0: rmsd against the docked pose is a real number or a named failure reason  -- rmsd=0.347951260632468, error=None

dock 0 seq 0: mean_plddt=97.1702807017544 iptm=0.93 rmsd=0.347951260632468
```

A mean pLDDT of 97.2 and ipTM of 0.93 are both high-confidence numbers, and an RMSD of 0.35 Å means
the predicted structure landed very close to the real docked pose -- on a sequence that was
designed, scored, and folded entirely fresh in this one run, not reused from any fixture.

See `artifact.json` in this folder for the full real result from the last cluster run.
