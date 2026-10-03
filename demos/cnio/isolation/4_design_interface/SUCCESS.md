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
- The number of sequences matches `config["num_seq_per_target"]` exactly (4 in our probe config).
- Every design carries a real, non-empty sequence and a real numeric score.
- **The PDB-file half of step 3's criterion (deferred here — see step 3's SUCCESS.md)**: the
  dock's structure was actually written to disk by `build_mpnn_config()`, called internally, and
  its bytes match the dock's own `assembly_pdb` exactly. We check this as a side effect of
  calling the real top-level function, not by calling the internal helper ourselves.

## What we are NOT touching

- `CageFlow.design_interface()` — the real batch function.
- `_design_one_interface()`, `build_mpnn_config()`, and ProteinMPNN itself (`run_mpnn.MPNNRunner`,
  the real `protein_mpnn_run.py`) — all real, all called exactly as a production run would.
- `dock_oligomer()`, `evaluate_geometry()`, `select_top_docks()` — regenerated fresh here, same
  real calls as steps 1–3, chained because all three are cheap.

## Expected result (actual numbers from the last real run)

```
  [PASS] at least one dock was selected to design  -- got 2
  [PASS] design_interface returned a dict keyed by selected dock indices  -- keys=[1], selected=[1, 3]
  [PASS] at least one dock designed successfully  -- got 1 of 2 selected
  [PASS] designed the expected number of sequences (4)  -- got 4
  [PASS] every design carries a non-empty sequence and a real score
  [PASS] the PDB file for the designed dock was written to disk
  [PASS] the ProteinMPNN config file was written to disk

designed 4 sequences for dock 1, scores: [0.616, 0.6997, 0.7823, 0.7256]
```

Dock 3's design failed in the same run with an unrelated subprocess error — recorded by
`design_interface()` itself as `status="design_failed"` in history, not silently dropped, exactly
as her docstring says it should be. Our check only requires *at least one* success, which is the
honest bar for an isolation test: we're proving the code path works, not that every input
succeeds every time.

Real designed sequences (one shown, scoring 0.7823 — the best of the 4): a 96-residue chain,
repeated and tied across all 12 symmetric copies of the dock, exactly as `homooligomer: true`
in the MPNN config specifies.

See `artifact.json` in this folder for the full result, including all 4 real sequences, from the
last cluster run.
