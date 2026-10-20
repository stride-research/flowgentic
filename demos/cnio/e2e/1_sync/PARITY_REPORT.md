# Track A vs Track B: parity report

**Scope.** Does replacing CageFlow's `Executor`/`Budget` with an AsyncFlow + Dragon
orchestrator (Track B, `flowgentic/`) change the science, relative to calling her documented
stage functions directly in her original sequential order (Track A, `original/`)? Both tracks
ran the same frozen config (`5dii_trimer.pdb`, 4 docks materialized, 2 selected, seeds pinned)
on the same node (`fortran`).

**Runs compared.** Track A: job 23xxx (2026-10-18, `wall_s=707`). Track B: job 24054
(2026-10-19, `wall_s=205`) — the current `main.py`, after two fixes (a closure bug that hung
every earlier run, and a `PYTHONPATH` ordering bug that silently changed which MSA ColabFold
folded against) and one simplification (removing an unneeded disk-spill layer). Raw files for
both are in `original/results/` and `flowgentic/results/`.

**Verdict up front: the science is the same.** `qualifying_sequences.csv` is byte-identical
between tracks (md5 `4c734ecb235e00f010986f534399eebb`). `history.jsonl`'s 10 records are
identical field-for-field after normalizing only timestamps, hostnames, and the out_dir prefix
baked into file paths. Every number below that a domain scientist would actually look at —
ProteinMPNN scores, Rosetta ddG, fold RMSD/pLDDT/ipTM/pTM — matches to full float precision.
See "What this match does and doesn't prove" before treating that as the end of the story.

---

## Domain (science) metrics

| Stage | Field | Track A | Track B | Match |
|---|---|---|---|---|
| select | docks selected | `[1, 2]` | `[1, 2]` | yes |
| design | dock 1: n_seq / best / mean MPNN score | 2 / 0.6626 / 0.67005 | 2 / 0.6626 / 0.67005 | yes |
| design | dock 2: n_seq / best / mean MPNN score | 2 / 0.6534 / 0.6614 | 2 / 0.6534 / 0.6614 | yes |
| rosetta | dock 1: group_ddG | -38.354002 | -38.354002 | yes |
| rosetta | dock 1: per_seq_ddG | [-48.2739, -28.4341] | [-48.2739, -28.4341] | yes |
| rosetta | dock 2: group_ddG | 1148.195822 | 1148.195822 | yes |
| rosetta | dock 2: per_seq_ddG | [1155.0188, 1141.3728] | [1155.0188, 1141.3728] | yes |
| fold | dock 1 seq 0: RMSD / pLDDT / ipTM / pTM | 20.3309 / 72.4645 / 0.49 / 0.54 | 20.3309 / 72.4645 / 0.49 / 0.54 | yes |
| fold | dock 1 seq 1: RMSD / pLDDT / ipTM / pTM | 5.7160 / 89.5135 / 0.86 / 0.89 | 5.7160 / 89.5135 / 0.86 / 0.89 | yes |
| fold | dock 2 seq 0: RMSD / pLDDT / ipTM / pTM | 5.9935 / 91.5100 / 0.87 / 0.89 | 5.9935 / 91.5100 / 0.87 / 0.89 | yes |
| fold | dock 2 seq 1: RMSD / pLDDT / ipTM / pTM | 2.3582 / 90.6000 / 0.87 / 0.89 | 2.3582 / 90.6000 / 0.87 / 0.89 | yes |
| fold | MSA used | `msa.a3m` (precomputed, fetched once) | `msa.a3m` (precomputed, fetched once) | yes |

Fold metrics live nested under each dock's `history.jsonl` record
(`design.fold.<seq_index>.{rmsd,mean_plddt,iptm,ptm}`) — they are not surfaced in
`qualifying_sequences.csv` (that table is Rosetta-side only, built from
`history_table.build_sequence_table()`) or in `compute_stats.json` (timing only). There is
currently no flat per-sequence fold table in either track; one would have to be built from
`history.jsonl` if that's wanted.

## System (resource) metrics

| Stage | Resource | Track A elapsed (n) | Track B elapsed (n) | Note |
|---|---|---|---|---|
| dock_setup | cpu | — (not its own stage in A) | 2.79s (1) | B times setup+scan as one task; A's equivalent cost is folded into its own startup, not broken out |
| prescore | cpu | 35.34s (510 tasks) | 32.82s (8 tasks) | **Same total work, different granularity.** A dispatches one task per solution through her `Executor`; B chunks into `N_PRESCORE_CHUNKS=8` AsyncFlow tasks. Wall-clock cost is comparable; task count is not a meaningful comparison across tracks. |
| dock_oligomer | cpu | 0.54s (4) | 0.70s (1) | A: one task per dock. B: one task, internal loop over 4 docks (DESIGN.md's "single-task" granularity for this stage). |
| evaluate_geometry | cpu | 0.41s (4) | 0.60s (1) | same granularity difference as above |
| select_top_docks | cpu | folded into A's sequential call, not separately timed | 0.16s (1) | — |
| design_interface | cpu | 84.87s (2) | 31.09s (2) | **Real, large gap.** Track A's own `cpu_per_elapsed=2.097` (>1.0 is only possible if her accounting counts multi-threaded CPU-seconds) suggests her `Executor` path is doing something B's direct `_design_one_interface` call isn't (or isn't accounting for the same way) — see caveats below before reading this as "FlowGentic is 2.7x faster at MPNN." |
| score_designs | cpu | folded into A's sequential call | 0.12s (1) | — |
| score_with_rosetta | cpu | 249.08s (4) | 255.42s (4) | **Match.** Same leaf worker, same arguments, same cost — exactly what should happen when nothing about dispatch changes the underlying compute. |
| fold_msa | cpu | 119.70s (4), only 11.84 actual cpu-s (cpu/elapsed=0.099) | *(not a separate stage in B)* | A measures the MSA fetch's network-wait separately and overlaps it with GPU work. B currently folds this wait time into `fold_oligomer`'s elapsed time — see caveats. |
| fold_oligomer | gpu | 212.46s (4) | 415.59s (4) | **Not a like-for-like number** — see caveats. Track B's figure includes the MSA-fetch wait that Track A reports separately; it is not evidence that ColabFold inference itself is slower under B. |
| **wall_s (total)** | | **707s** | **205s** | 3.4x. With zero real concurrency in B yet (every stage still `await`s the previous one; see `main.py`'s docstring), this gap is almost entirely dispatch/bookkeeping overhead in Track A's `Executor`/`Budget` path, not parallelism. |

### Caveats on the system-metrics comparison, read before citing any of it

1. **Track B's `compute_stats.json` is partly placeholder data, not a measurement.** `cpu_per_elapsed` is a flat `1.0` and `peak_rss_mb_max` is a flat `0.0` for every single row — `main.py`'s own `_compute_row()` docstring says so explicitly: those two fields need `psutil`/`resource` sampling inside each task body, which was never added. Track A's numbers for the same two fields *are* real (164MB/2035MB/14627MB RSS, cpu/elapsed ratios that vary by stage) because they come from her `Budget`'s own accounting. **Any claim about Track B's CPU efficiency or memory footprint is currently unsupported — the column exists, the number in it does not mean anything yet.**

2. **`fold_msa` vs `fold_oligomer` split is asymmetric between tracks**, and this directly explains most of the apparent `fold_oligomer` slowdown. Track A's CageFlow splits the MSA fetch (CPU-bound, network-wait-dominated, her own docstring: "~40s of the scarcest resource... sat idle on a server round-trip") into its own stage, specifically so the wait doesn't get billed to GPU time. Track B's `task_fold` currently does both in one task, so its `fold_oligomer` elapsed time includes the same network wait, just mislabeled as GPU-bound. The fix is mechanical (split `task_fold` into two tasks, the way the rest of the file already separates concerns) — not yet done, flagged here rather than silently absorbed into a "B's GPU work is slower" conclusion that the data doesn't actually support.

3. **`design_interface`'s 84.87s vs 31.09s gap is unexplained, not attributed.** Same leaf worker (`_design_one_interface`), same arguments, same ProteinMPNN config, same GPU. The two most likely explanations — Track A's `Executor` adding real dispatch/bookkeeping overhead, or Track A's `cpu_per_elapsed=2.097` reflecting genuinely different thread/process accounting — are both plausible and neither has been verified by rerunning either track multiple times. Treat the headline wall-clock win (707s → 205s) as directionally real (it is large, and `score_with_rosetta`'s near-identical number across tracks is a useful sanity check that *not everything* is skewed), but do not quote a precise multiplier for any individual stage until this is isolated.

4. **`budget_summary.json` and `cageflow.log` exist only for Track A.** This is by design, not a missing artifact — Track B never instantiates her `Budget` or `Executor` (that's the entire point of the rewrite; see `DESIGN.md`), so there is nothing for those files to report. `compute_stats.json` is Track B's replacement, once caveat 1 above is fixed.

---

## Is this a fair test of reproducibility, or just "the same deterministic function called twice"?

This matters for how much confidence to take from the exact match above, and the honest answer
is: **it's real evidence, but it is not yet evidence about noise.**

**Every stochastic component in this pipeline was seeded identically in both runs**, confirmed
directly from `config.used.json`: `fold_seed=20260831`, `rosetta_seed=20260831`, and
ProteinMPNN's seed is hardcoded to `37` in her `run_mpnn.py` regardless of config. Both tracks
called the exact same leaf workers (`_design_one_interface`, `_score_one_sequence_rosetta`,
`_fold_one_sequence`, ...) with the exact same arguments, on the same GPU model, same software
versions, same scaffold. Given all of that, **bit-identical output is the *expected*, correct
result of a deterministic computation run twice — not a surprising coincidence, and not proof
that the pipeline is robust to its actual stochastic noise floor.** A match here mainly rules
out a *structural* bug (wrong arguments, wrong dock, dropped sequence, silently different code
path) — which is exactly the class of bug the MSA/PYTHONPATH issue turned out to be, and exactly
why this comparison caught it. It was never going to tell us anything about run-to-run variance,
because run-to-run variance was deliberately switched off for this comparison.

**We already have a measurement of what that variance actually looks like, from Laura**, and it
is large: her own measured Rosetta run-to-run noise (same sequence, same settings, unseeded) was
**heteroscedastic** — 0.092 kcal/mol stdev for one sequence, 1.055 kcal/mol for another, an 11x
spread between them (see `context/pipeline-operations.md`). Dock 2's `group_ddG=1148.2` in this
run is far outside any plausible noise band regardless; dock 1's `group_ddG=-38.4` is the kind
of number where a 1 kcal/mol-scale noise floor would matter to an interpretation. ColabFold/AF2
multimer inference has its own non-determinism sources (GPU kernel scheduling, any MSA-server
round trip) that a fixed `fold_seed` does not fully control, independent of Rosetta's.

**What this means going forward, concretely:**
- This report's exact match is the right outcome for *this* test and should be read as "no
  structural regression found," not "the pipeline is noise-free."
- To actually characterize whether Track B's *distribution* of outcomes matches Track A's
  (the comparison that would matter once stage 2 removes barriers and stage 3 reasons over
  resource-vs-quality tradeoffs), either track needs to be run **several times** — ideally with
  seeding turned off, or deliberately varied — and the spread characterized the way Laura
  already did for Rosetta. That has not been done for either track yet.
- The two real divergences this comparison *did* catch (the hang, and the MSA/PYTHONPATH bug)
  were both structural, not noise — which is consistent with the above: a single seeded A/B run
  is a sharp tool for finding "did the code path change," and a blunt one for "did the noise
  profile change."
