# Reading `metrics.json`

One schema (`schema.py`), every run. A section is `null` when the concept does
not exist for that run's executor — not when we forgot to measure it.
`collect.py` only reads her documented output files; it never imports
`CageFlow`.

## Always populated — her own instrumentation, any executor

| Field | Obtained from | Why it matters | Read it against |
|---|---|---|---|
| `compute[].elapsed_s_mean/max` | `compute_stats.json`, her `Budget.compute_report()` | Real per-stage wall time, independent of which backend ran it | `overhead[].exec_s_mean` should roughly match, once that section exists — if it doesn't, the two instrumentation paths disagree and that's worth chasing |
| `compute[].cpu_per_elapsed` | same | `>1`: internal multi-threading (torch/BLAS/PyRosetta). `~1`: single-threaded. `«1`: mostly waiting, not computing | A stage with low `cpu_per_elapsed` and high `queue_wait_s` (once available) was waiting on something external, not CPU-bound |
| `compute[].peak_rss_mb_max` | same | Per-worker-**process** high-water mark — what to size `--mem` against | This is job-isolated. `node_resources[].memory_percent_mean` is not (see below) — prefer this one for any sizing claim |
| `budget[].spent_hours` | `budget_summary.json`, her `Budget.summary()` | CPU-/GPU-hours actually charged for task time | Compare to `allocation[].core_hours`/`gpu_hours` — the gap between spent and allocated is the headline resource number |
| `science.*` | `history.jsonl` + `qualifying_sequences.csv` | The actual result — ddG, pLDDT, ipTM, RMSD, every `METRIC_KEYS` field | See the three-family caveat below before quoting any Rosetta number |

## Populated only by a backend that reserves or reports — `null` otherwise

| Field | Obtained from | Why it matters | Read it against |
|---|---|---|---|
| `allocation[].core_hours/gpu_hours` | `allocation_stats.json`, her `Executor.allocation_report()` — empty `{}` under `SequentialExecutor`, real `sacct` data only from `DaskSlurmExecutorBackend` or a cluster-reserving backend | What the **cluster** was charged, as opposed to what the science cost. Her own measured example: a cpu pool held ~3h for 367s of task time (~30:1), blocked waiting on a gpu worker | `allocation.wall_s` vs `compute.elapsed_s_total` for the same resource — the ratio is literally the thesis's headline resource-utilization number |
| `pool_utilization[].utilization` | **derived by us**, not emitted by RHAPSODY — from lifecycle timestamps in the telemetry JSONL (`mean_concurrency`) against the pool size we configured (`pool_size`) | How busy a worker pool actually was | Expect `≈ 1/pool_size` on a straight-line v1 DAG — that's a real baseline, not a bug. Watch it rise once v2 adds cross-dock concurrency |
| `overhead[].overhead_s_mean` | **derived by us** — `e2e_s − exec_s`, from `TaskSubmitted/Queued/Started/Completed` event deltas | Framework cost around the science | `null` for the reference run because `SequentialExecutor.submit()` runs the function inline — there is no queue, no distinct states, nothing to measure, not a small unmeasured number. Meaningful once tasks actually contend for a pool (v2) |
| `node_resources[].cpu_percent_mean` etc. | RHAPSODY `ResourceUpdate` events, psutil/pynvml polling, confirmed live on `haskell` | Node-level time series, including real per-GPU utilization | **Caveat, not small**: node-level, not job-level. `scitech`'s partitions carry `OverSubscribe=FORCE:2`, so another user's work can share the node and inflate these numbers. Prefer `compute[].peak_rss_mb_max`/`cpu_per_elapsed` for any job-isolated claim; treat this section as context, not as an allocation justification on its own |

## The one thing worth re-reading before quoting any Rosetta number

`science.rosetta[].metrics` holds three distinct families, and conflating them
is a real mistake, not a style nit:

- **unprefixed** (`dG_separated`, `shape_complementarity`, ...) — copy 0 against
  *every* other copy. Merges the point group's symmetry-equivalent interfaces
  into one number. Fine for ranking, wrong as an absolute.
- **`pair_*`** — the same fields, recomputed on **one isolated oligomer pair**.
  Her measured example, same pose: `shape_complementarity` 0.254 (merged) vs
  0.435 (pair); `dSASA` 3782 vs 2252.
- **`dG_int_symmetric`** — Rosetta's own symmetric interface energy, counted off
  the symdef rather than a chosen chain split.

State which family a reported number belongs to. Every time.
