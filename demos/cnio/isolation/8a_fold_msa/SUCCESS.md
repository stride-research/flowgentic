# Step 8a: `fold_msa` (real ColabFold MSA fetch) — success criteria

## What this step is, in plain terms

A designed sequence folds better if we tell the fold model what related sequences look like
across evolution (a "multiple sequence alignment", MSA) rather than just the bare sequence. This
step fetches that alignment from the public ColabFold MMseqs2 server for one real designed chain
and writes it to disk as a `.a3m` file. It is the CPU half of fold — split out from the GPU half
(step 8b) because the fetch is a network wait, not compute, and Laura measured it idling a GPU
for ~40s on a real run. A failed fetch here is not fatal to the pipeline: `fold_oligomer()` falls
back to fetching inline on the GPU worker, so this step tests an optimization, not a gate.

## What we are checking

- `_fetch_one_msa()` reports `ok: True` (the uniform success/failure wrapper every CageFlow worker
  uses).
- The `.a3m` file actually exists on disk at the path it returned.
- The `.a3m` header matches ColabFold's own encoding of chain count and length:
  `#<chain length>\t<n_chains>` — here `#95\t3`, a real homotrimer of a 95-residue chain. This is
  the exact mechanism `colabfold_batch` (step 8b) uses to learn it's folding 3 copies of one
  chain, not one 287-residue chain — getting it wrong caused a real pipeline failure before (see
  below), so checking it here is not a formality.

## What we are NOT touching

- `fetch_msa()` / `colabfold_msa.py` (runs inside ColabFold's own conda env, calls
  `colabfold.colabfold.run_mmseqs2` against `api.colabfold.com`) — real, unmodified, hits the real
  public server.
- `msa_to_str()` — ColabFold's own header-writing logic, not reimplemented or hand-checked against
  a formula; we only assert the real output matches the real input shape.

## A real bug this found, not an environment issue

The first version of this script built `seq` by `":".join(copies)` — all 3 identical 95-residue
chains joined into one 287-character string — because `input.fasta` itself stores the designed
sequence that way (one physical chain per `:`-separated entry). That is the **wrong shape** for
`fetch_msa()`/`_fold_one_sequence()`: both take **one chain's sequence** plus `n_chains` as a
separate cardinality argument, and repeat/tie it internally. Confirmed three ways before fixing,
not assumed:

1. `CageFlow.fold_oligomer()` builds this exact call with `seq = _oligomer_chain_seq(...)`, and
   `mpnn_io.oligomer_chain_seq()`'s own docstring says it exists precisely to avoid this: *"ProteinMPNN
   reports a design as a single string spanning every chain... fold_oligomer() folds ONE oligomer,
   so it needs one chain's sequence repeated n_chains times, not the whole assembly."* It adds that
   this is not hypothetical — a prior real run died 3 minutes into an MSA fetch because the
   unsplit string reached ColabFold and hit `Invalid character in the sequence: /` from
   ProteinMPNN's own `/`-joined multi-chain output.
2. `colabfold_msa.py`'s own docstring: *"ONE unique sequence with cardinality n_chains."*
3. Running it the wrong way first and observing the real failure mode: it didn't crash, it
   silently fetched an MSA for a fake 287-residue "chain" and wrote a header of `#287\t3` instead
   of `#95\t3` — a shape bug that looks like success unless you check the header against the real
   chain length, which is exactly why that check exists above.

Fixed by taking `copies[0]` (one real chain) instead of rejoining all three.

## Expected result (actual numbers from the last real run)

```
  [PASS] MSA fetch reported ok  -- None
  [PASS] the .a3m file exists  -- .../8a_fold_msa/_scratch/msa.a3m
  [PASS] the .a3m header matches the real criterion ('#95\t3')  -- got '#95\t3'

MSA fetched: .../8a_fold_msa/_scratch/msa.a3m, header='#95\t3'
```

See `artifact.json` in this folder for the full real result from the last cluster run.

## An environment bug this also found (fixed, not CageFlow's)

`run.sh` does `module load PyTorch/2.1.2-foss-2023a` (needed because `CageFlow.py` imports
`pandas` transitively via `history_table.py`). That module sets `PYTHONPATH` to several EESSI
`python3.11` site-packages, including `SciPy-bundle`'s numpy. `_fetch_one_msa()` spawns
ColabFold's own self-contained `python3.10` as a subprocess, which inherits `os.environ` wholesale
(`fold.py`'s `subprocess.run` has no `env=` override) — so without a fix, that subprocess picked
up the EESSI module's numpy (built for a different Python ABI) instead of its own bundled one,
crashing with `ImportError: No module named 'numpy.core._multiarray_umath'`. Fixed on our side only
— `main.py` does `os.environ.pop("PYTHONPATH", None)` right after importing `CageFlow` (by which
point Python has already cached the `pandas`/`numpy` imports our own process needs, so dropping
`PYTHONPATH` afterwards is safe), before calling into `_fetch_one_msa`. Nothing in `CageFlow.py`,
`fold.py`, or `colabfold_msa.py` was touched.
