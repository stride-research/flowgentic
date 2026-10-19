# export_af3_jobs — success criteria

## What this is

`CageFlow.export_af3_jobs(designed_oligomers, docked_oligomers, rosetta_scores, config, out_dir)`
(`CageFlow.py:3319`, new in `0d1650a`), called in isolation with the same
dock → geometry → select → design → score_designs → score_with_rosetta chain step 6 already
builds, since it needs `designed_oligomers`/`rosetta_scores` and nothing fold-specific.

**Not a successor to step 7.** In the real pipeline (`_run_cage_flow_steps`) this runs
unconditionally right after `fold_oligomer()` ("Step 6b"), regardless of whether
`config["fold_oligomers"]` is set. Her own docstring: *"A run that turned folding OFF — or
whose folds timed out — is precisely the run whose sequences most need to leave the cluster, so
this is unconditional and cheap rather than gated on `config['fold_oligomers']`."* Both step 7
and step 8 are fed by the identical `postredesign_selection` group (`fold_candidates()`), so a
fold and an AF3 job always describe the same molecule — this isolation step just doesn't need
step 7's fold result to prove that.

## Why this step exists

Added 2026-10-16, found while auditing what changed in `0d1650a` beyond the seven original
isolation steps. `export_af3_jobs` didn't exist when steps 1–7 were first written; it's genuinely
new pipeline behavior with zero prior isolation coverage. Her own `_run_cage_flow_steps`'s
"Step 8: Collapse to single metric" comment, by contrast, has no code under it at all — an open
design question (`context/open-questions.md` #16, "no single objective score in the domain"),
not an implementation gap. Nothing to isolate-test there yet.

## What passing looks like

- `export_af3_jobs` returns a non-empty manifest (list of dicts, one per exported job).
- `af3/af3_server_ALL.json` and `af3/manifest.csv` exist on disk.
- Per manifest row: `af3/af3_local/<job_name>.json` exists, and `tokens` is a positive int.

## What we are NOT touching

`CageFlow.py`, `af3_export.py`, `postredesign_selection.py`, `fold_candidates`. Zero changes to
her code — same as every other isolation step.

## Open flag, not yet resolved

Reading `fold_oligomer`'s docstring while building this step surfaced a tension worth raising
with the team: her comment on `FOLD_UNITS` says `fold_unit="cage"` is *"the only version of the
fold that can say anything about the designed interface: a confident trimer proves nothing,
because the building block is a native PDB entry the predictor has seen."* We pinned
`fold_unit="oligomer"` in the frozen config specifically to avoid the >5000-residue whole-cage
fold Ruben ruled out of scope — but her own example for `"cage"` is "12 chains for a
one-component T cage," which at 5dii's ~194 residues/chain is ~2,328 residues, well under that
threshold. Worth checking whether "oligomer" was the right call for OUR scaffold specifically,
separate from the much larger assemblies Ruben was actually ruling out.
