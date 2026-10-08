# The reference run — success criteria

## What this is

Her real entry point, unmodified, nothing of ours in the execution path:

```bash
python CageFlow.py config.json
```

confirmed against her own source (`CageFlow.py`'s `__main__` block is literally
`run_cage_flow(_load_config_or_exit(sys.argv[1] ...))`) — this is not a convention
we picked, it's how she runs her own pipeline. `run_cage_flow()` calls
`_run_cage_flow_steps()`, which chains `dock_oligomer → evaluate_geometry →
select_top_docks → design_interface → score_designs → score_with_rosetta →
fold_oligomer`, then writes `qualifying_sequences.csv`/`.html`,
`budget_summary.json`, and `compute_stats.json` — all of it her code, none of it
chained by us the way the earlier isolation steps did.

This run is the fixed point everything else gets diffed against: once we build
the AsyncFlow-driven run, its science output should be **identical** to this
one (same stages, same order, same config), not merely close. Any divergence is
ours to explain.

## Config

`config.json` — every field explicit, nothing implied. Differences from her
`config.1c5e_probe.json`, each individually proven on this cluster before being
combined here:

- `n_docks=2`, `max_materialized=4`, `num_seq_per_target=2` (not 1) — bumped up
  from the smallest possible values specifically so real branching is exercised,
  not bypassed: `score_with_rosetta`'s per-dock dedup and `fold_oligomer`'s
  global dedup/ranking both need ≥2 candidates to ever run their actual logic.
- `fold_max_total=1` — a real code path (`_rank_fold_candidates`, cross-dock
  ranking) that no isolation script had exercised before this run, since none
  of them ever set it.
- `fold_oligomers=true`, five path overrides (`cage_designer_path`, `mpnn_dir`,
  `colabfold_bin/_data/_python`) pointing at our cluster clones instead of hers.
- `executor="sequential"` — no AsyncFlow, no `dask_slurm`. This is Track A.

## What we are NOT touching

`CageFlow.py`, every function it calls, `history_io.py`, `history_table.py`,
`budget.py`, `executors.py`. The whole repo. `run.sh` only `cd`s into it and
invokes the CLI.

## A bug this found — in our collector, not in her code

`metrics/collect.py` folds `history.jsonl`'s per-dock records with a plain
`dict.update()` at first, which looked reasonable until run against her own
committed `reference/laura_history.jsonl`: it reported **0** Rosetta-scored
sequences where there were actually 2. Root cause: her `design` sub-dict
carries *different* fields per status (`designed` has `n_sequences`;
`rosetta_scored` adds `per_seq_metrics`; `folded` has only `fold`/`n_folded`),
and a later record's `design` dict was silently clobbering the earlier one's
`per_seq_metrics` entirely. Fixed by merging nested dicts key-by-key instead of
replacing them. Caught by testing the collector against real data before
trusting its output on a fresh run — exactly why `laura_history.jsonl` is kept
in this repo as a fixture.

## Expected result (actual numbers from the last real run)

```
<<FILLED IN AFTER THE CLUSTER RUN>>
```

See `metrics.json` (written by `../../metrics/collect.py`) for the full result
in the fixed schema, and raw `history.jsonl`/`qualifying_sequences.csv` in
`out_dir` for her own original output.
