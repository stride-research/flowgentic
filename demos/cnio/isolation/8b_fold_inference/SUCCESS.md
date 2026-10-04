# Step 8b: `fold_inference` (real ColabFold structure prediction) — success criteria

## What this step is, in plain terms

Step 8a fetched evolutionary context for a designed sequence; this step is the actual fold
prediction — ColabFold's AlphaFold2-multimer model takes that sequence + alignment and predicts a
3D structure, with confidence scores. This is the GPU half of fold, and it is **record-only in
the pipeline, never a gate** — nothing downstream reads its output to pass/fail a design; it's
evidence a scientist reviews.

Plain-English glossary for the four numbers below:
- `mean_plddt` (0-100): the model's own per-residue confidence in its predicted structure,
  averaged. Above ~90 is "very confident," below ~50 is "don't trust this region."
- `ptm` / `iptm` (0-1): confidence in the overall fold / specifically in how the chains interface
  with each other. Higher is better; above ~0.8 is considered a strong complex prediction.
- `rmsd`: how far the predicted structure's backbone deviates (in Angstrom) from the real docked
  pose it was designed against. Lower is better; this is a geometric sanity check, not a
  confidence score — a design could have a genuinely different final shape, which is the
  biologically interesting case, not a failure.

## What we are checking

- `_fold_one_sequence()` reports `ok: True`.
- A real `.pdb` structure file was actually written to disk.
- `mean_plddt` is a real float (not `None`, not a placeholder).
- `rmsd` against the docked pose is either a real float, or a named failure reason (a chain-count
  mismatch is reported this way rather than crashing — see `_fold_one_sequence`'s own docstring:
  "a failed RMSD is reported separately from a failed fold").

## What we are NOT touching

- `fold_sequence()` / `colabfold_batch` itself — real, unmodified, runs the actual
  AlphaFold2-multimer model on a GPU.
- `oligomer_rmsd()` — real, unmodified structural comparison.

## Inputs, and how they were reduced without reducing the science

- The designed sequence and its real alignment are reused from dock 44's own committed historical
  run (`fold_seq_20/input.fasta`, `fold_seq_20/msa.a3m`) — this step tests **inference**, and step
  8a already independently tested the **fetch** that produced that exact alignment, so reusing it
  here isn't a shortcut, it's the natural division of labor between the two steps.
- `native_pdb_text` uses the same minimal stand-in as step 7: the raw scaffold PDB text
  (`1c5e_trimer.pdb`), because that's genuinely all `fold_oligomer()` ever sources for this
  argument (`docked_oligomer["copies"][0]`) — not a simplification we introduced.
- `fold_num_models: 1` (from the probe config) means ColabFold predicts one structure, not five —
  her own config's choice for a cheap sanity run, not ours.

## A real bug this found (same root cause as step 8a)

Same shape bug as step 8a: the first version passed the `:`-joined 287-character assembly string
instead of one real 95-residue chain. `fold_sequence()` calls
`write_multimer_fasta(seq, n_chains, ...)`, which repeats `seq` internally `n_chains` times — so
the fix is identical, use `copies[0]`. See step 8a's `SUCCESS.md` for the full derivation (docstring
citations for both `_oligomer_chain_seq` and `colabfold_msa.py`, plus the real prior production
failure this exact bug already caused once: `Invalid character in the sequence: /`).

## Expected result (actual numbers from the last real run)

```
  [PASS] fold reported ok  -- None
  [PASS] a real PDB was written  -- .../8b_fold_inference/_scratch/msa_unrelaxed_rank_001_alphafold2_multimer_v3_model_1_seed_20260831.pdb
  [PASS] mean_plddt is a real number  -- 97.2658596491228
  [PASS] rmsd against the docked pose is a real number or a named failure reason  -- rmsd=0.5379150704937897, error=None

fold: mean_plddt=97.2658596491228 iptm=0.93 rmsd=0.5379150704937897
```

A mean pLDDT of 97.3 and ipTM of 0.93 are both high-confidence numbers, and an RMSD of 0.54 Å means
the predicted structure landed very close to the real docked pose — consistent with this being a
genuinely well-designed homotrimer interface, not a coincidence of the test setup (these numbers
come from calling ColabFold on real data, nothing about the harness could produce them).

See `artifact.json` in this folder for the full real result from the last cluster run.
