# Step 4: `design_interface` (ProteinMPNN) — success criteria

## What this step is, in plain terms

A selected dock (step 3) is a fixed 3D shape — the backbone is set, but the actual amino acids
at the interface between the 3 (or more) copies haven't been chosen yet. This step calls
ProteinMPNN, a neural network, to propose several different amino-acid sequences that could sit
at those interface positions and hold the copies together. Each proposal is a "design." This is
the first real machine-learning inference in the pipeline, as opposed to physics/geometry math.

## A real environment gap this surfaced, worth recording

The first run failed with `ModuleNotFoundError: No module named 'torch'`. ProteinMPNN's own
script needs PyTorch, and it runs as a **subprocess using whatever Python is already running the
main script** (`run_mpnn.py`'s `_python()` just returns `sys.executable` — there is no separate
interpreter it can be pointed at). The module we'd been using for steps 1–3
(`SciPy-bundle/2023.11-gfbf-2023b`) only has numpy/scipy, not torch.

Fixed by switching to `PyTorch/2.1.2-foss-2023a` instead — one EESSI module that provides torch
**and** numpy/scipy together (confirmed: `torch 2.1.2, numpy 1.25.1, scipy 1.11.1`), so it
replaces `SciPy-bundle` cleanly for every step, not just this one. All `run.sh` scripts were
updated to this single module for consistency.

## What we are checking

- `design_interface()` returns a dict keyed only by the dock indices it actually designed.
- At least one dock designed successfully.
- **For every dock that designed successfully** (not just the first — see below): the number of
  sequences matches `config["num_seq_per_target"]` exactly (4 in our probe config), every design
  carries a real non-empty sequence and a real numeric score, and the PDB/config side effects
  (next bullet) are present.
- **The PDB-file half of step 3's criterion (deferred here — see step 3's SUCCESS.md)**: the
  dock's structure was actually written to disk by `build_mpnn_config()`, called internally, and
  its bytes match the dock's own `assembly_pdb` exactly. We check this as a side effect of
  calling the real top-level function, not by calling the internal helper ourselves.

## Why every dock, not just one

An earlier version only inspected one arbitrary dock. That misses something real: docks genuinely
differ in interface size — this run shows dock 1 with 432 design positions and dock 3 with 396 —
so confirming the checks hold for one dock says nothing about whether they hold for another. The
sequence *count* is config-driven and so is guaranteed uniform across docks by construction; the
sequences themselves are not, and only checking every dock that actually succeeded catches a bug
that might only manifest for one dock's specific interface shape.

## What we are NOT touching

- `CageFlow.design_interface()` — the real batch function.
- `_design_one_interface()`, `build_mpnn_config()`, and ProteinMPNN itself (`run_mpnn.MPNNRunner`,
  the real `protein_mpnn_run.py`) — all real, all called exactly as a production run would.
- `dock_oligomer()`, `evaluate_geometry()`, `select_top_docks()` — regenerated fresh here, same
  real calls as steps 1–3, chained because all three are cheap.

## Expected result (actual numbers from the last real run)

```
  [PASS] at least one dock was selected to design  -- got 2
  [PASS] design_interface returned a dict keyed by selected dock indices  -- keys=[1, 3], selected=[1, 3]
  [PASS] at least one dock designed successfully  -- got 2 of 2 selected
  [PASS] dock 1: designed the expected number of sequences (4)  -- got 4
  [PASS] dock 1: every design carries a non-empty sequence and a real score
  [PASS] dock 1: the PDB file was written to disk and matches the dock
  [PASS] dock 1: the ProteinMPNN config file was written to disk
  [PASS] dock 3: designed the expected number of sequences (4)  -- got 4
  [PASS] dock 3: every design carries a non-empty sequence and a real score
  [PASS] dock 3: the PDB file was written to disk and matches the dock
  [PASS] dock 3: the ProteinMPNN config file was written to disk

designed 4 sequences for dock 1, scores: [0.616, 0.6997, 0.7823, 0.7256]
designed 4 sequences for dock 3, scores: [0.5521, 0.5986, 0.6906, 0.6224]
```

A previous run had dock 3 fail with a transient, unrelated subprocess error — recorded by
`design_interface()` itself as `status="design_failed"` in history, not silently dropped, exactly
as her docstring says it should be. Our check only requires *at least one* success for the whole
step, which is the honest bar for an isolation test: we're proving the code path works, not that
every input succeeds every time.

Real designed sequences (dock 1's best, scoring 0.7823): a 96-residue chain, repeated and tied
across all 12 symmetric copies of the dock, exactly as `homooligomer: true` in the MPNN config
specifies. Dock 1 and dock 3 get genuinely different sequences — different interface, different
design.

See `artifact.json` in this folder for the full result, including all 4 real sequences, from the
last cluster run.
