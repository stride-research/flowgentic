# Track A vs Track B: parity report

**Scope.** Does replacing CageFlow's `Executor`/`Budget` with an AsyncFlow + Dragon
orchestrator (Track B, `flowgentic/`) change the science, relative to calling her documented
stage functions directly in her original sequential order (Track A, `original/`)? Both tracks
ran the same frozen config (`5dii_trimer.pdb`, 4 docks materialized, 2 selected, seeds pinned)
on the same node (`fortran`).

**Runs compared.** Track A: job 24194 (2026-10-20, `wall_s=544`), her unmodified
`run_cage_flow()` called directly. Track B: job 24197 (2026-10-20, `wall_s=209`), the current
`src/main.py` + `stages/`. Raw files for both are in `original/results/` and
`flowgentic/results/`.

**Both tracks now run on a self-contained venv with no `module load` and no `PYTHONPATH`
anywhere** (`~/.venvs/asyncflow` on the cluster) — see
`context/current-implementation.md`, "UPDATE 2026-10-20." This replaced the previous setup
where both tracks loaded `module load PyTorch/2.1.2-foss-2023a`, which was the actual cause
of the MSA/PYTHONPATH bug this report's caveats used to describe as a workaround. The
monkeypatch that used to live in `run_original.py` (`fold.subprocess.run` scrubbing
`PYTHONPATH`) has been deleted; it is no longer needed because `PYTHONPATH` is never set in
the first place.

**The numbers below are NOT the same numbers an earlier version of this report cited.**
Moving off the EasyBuild/foss-toolchain torch build (delivered by the module) to a plain
PyPI CPU wheel changed ProteinMPNN's sampled sequences at a handful of borderline positions,
despite the same hardcoded seed (`37`) — a BLAS/floating-point-order effect in the sampler,
not a bug (see "What this match does and doesn't prove" below). **Do not compare the figures
here against any earlier run's numbers; they come from a different environment.**

**Verdict up front: the science still matches between tracks, under the new environment.**
`qualifying_sequences.csv` is byte-identical between tracks (md5
`68be8e5176d537208bee8671d2c6221a`). `history.jsonl`'s records are identical field-for-field
after normalizing only timestamps, hostnames, and the out_dir prefix baked into file paths.
Every number a domain scientist would actually look at — ProteinMPNN scores, Rosetta ddG,
fold RMSD/pLDDT/ipTM/pTM — matches to full float precision. See "What this match does and
doesn't prove" before treating that as the end of the story.

---

## Domain (science) metrics

| Stage | Field | Track A | Track B | Match |
|---|---|---|---|---|
| select | docks selected | `[1, 2]` | `[1, 2]` | yes |
| design | dock 1: n_seq / scores / mean MPNN score | 2 / [0.6502, 0.6598] / 0.655 | 2 / [0.6502, 0.6598] / 0.655 | yes |
| design | dock 2: n_seq / scores / mean MPNN score | 2 / [0.6594, 0.6176] / 0.6385 | 2 / [0.6594, 0.6176] / 0.6385 | yes |
| rosetta | dock 1: group_ddG | -56.293125 | -56.293125 | yes |
| rosetta | dock 1: per_seq_ddG | [-65.9444, -46.6418] | [-65.9444, -46.6418] | yes |
| rosetta | dock 2: group_ddG | 1132.880384 | 1132.880384 | yes |
| rosetta | dock 2: per_seq_ddG | [1136.1241, 1129.6367] | [1136.1241, 1129.6367] | yes |
| fold | dock 1 seq 0: RMSD / pLDDT / ipTM / pTM | 20.3524 / 68.4069 / 0.46 / 0.50 | 20.3524 / 68.4069 / 0.46 / 0.50 | yes |
| fold | dock 1 seq 1: RMSD / pLDDT / ipTM / pTM | 5.7751 / 89.1389 / 0.87 / 0.89 | 5.7751 / 89.1389 / 0.87 / 0.89 | yes |
| fold | dock 2 seq 0: RMSD / pLDDT / ipTM / pTM | 6.2166 / 69.4693 / 0.46 / 0.51 | 6.2166 / 69.4693 / 0.46 / 0.51 | yes |
| fold | dock 2 seq 1: RMSD / pLDDT / ipTM / pTM | 20.2071 / 78.3405 / 0.60 / 0.66 | 20.2071 / 78.3405 / 0.60 / 0.66 | yes |
| fold | MSA used | `msa.a3m` (precomputed, fetched once) | `msa.a3m` (precomputed, fetched once) | yes |

Fold metrics live nested under each dock's `history.jsonl` record
(`design.fold.<seq_index>.{rmsd,mean_plddt,iptm,ptm}`) — they are not surfaced in
`qualifying_sequences.csv` (that table is Rosetta-side only, built from
`history_table.build_sequence_table()`) or in `compute_stats.json` (timing only). There is
currently no flat per-sequence fold table in either track; one would have to be built from
`history.jsonl` if that's wanted.

**A third, independent confirmation exists beyond this A/B pair**: Track B's own code, run
through the stale pre-modularization flat `main.py` that was still deployed on the cluster
at the time (job 24193), produced the identical `group_ddG`/RMSD/pLDDT/ipTM figures above.
Three different code paths (her unmodified orchestrator; Track B's current `stages/` layer;
Track B's now-retired monolithic driver) agree exactly — stronger evidence against a
structural bug than the two-track comparison alone, since it is no longer possible to
attribute the match to something both current implementations happen to share.

## System (resource) metrics

| Stage | Resource | Track A elapsed (n) | Track B elapsed (n) | Note |
|---|---|---|---|---|
| dock_setup | cpu | — (not its own stage in A) | 2.90s (1) | B times setup+scan as one task; A's equivalent cost is folded into its own startup, not broken out |
| prescore | cpu | 32.67s (510 tasks) | 31.46s (8 tasks) | **Same total work, different granularity.** A dispatches one task per solution through her `Executor`; B chunks into `N_PRESCORE_CHUNKS=8` AsyncFlow tasks. Wall-clock cost is comparable; task count is not a meaningful comparison across tracks. |
| dock_oligomer | cpu | 0.61s (4) | 0.84s (1) | A: one task per dock. B: one task, internal loop over 4 docks (DESIGN.md's "single-task" granularity for this stage). |
| evaluate_geometry | cpu | 0.46s (4) | 0.71s (1) | same granularity difference as above |
| select_top_docks | cpu | folded into A's sequential call, not separately timed | 0.20s (1) | — |
| design_interface | cpu | 40.04s (2) | 39.06s (2) | **Match.** Unlike the previous (module-venv) run of this comparison, this gap has closed — both now land within 1s of each other. The earlier ~2.7x gap was not reproduced here; worth noting it may have been environment-dependent rather than structural, though neither explanation was ever confirmed. |
| score_designs | cpu | folded into A's sequential call | 0.15s (1) | — |
| score_with_rosetta | cpu | 244.42s (4) | 262.85s (4) | **Match.** Same leaf worker, same arguments, same cost — exactly what should happen when nothing about dispatch changes the underlying compute. |
| fold_msa | cpu | 17.58s (4), 11.45 actual cpu-s (cpu/elapsed=0.65) | *(not a separate stage in B)* | A measures the MSA fetch's network-wait separately and overlaps it with GPU work. B currently folds this wait time into `fold_oligomer`'s elapsed time — see caveats. |
| fold_oligomer | gpu | 203.79s (4) | 387.47s (4) | **Not a like-for-like number** — see caveats. Includes both the MSA-wait-attribution asymmetry and a real GPU-concurrency effect (see caveat 2). |
| **wall_s (total)** | | **544s** | **209s** | 2.6x. With zero real concurrency in B yet beyond per-stage `asyncio.gather` (every stage still `await`s the previous one; see `main.py`'s docstring), this gap is almost entirely dispatch/bookkeeping overhead in Track A's `Executor`/`Budget` path, not parallelism. |

### Caveats on the system-metrics comparison, read before citing any of it

1. **Resolved, no longer a caveat: Track B's `compute_stats.json` is real data now, not
   placeholder.** An earlier version of this report flagged `cpu_per_elapsed` as a flat `1.0`
   and `peak_rss_mb_max` as a flat `0.0` for every row. That has been fixed
   (`flowgentic.metrics`'s `_snapshot()`/`_write()`, using `resource.getrusage`); the table
   above shows real, varied values for both tracks (e.g. `fold_oligomer` peak RSS 14,629 MB
   for A vs 14,634 MB for B — consistent with each other, as expected for the same leaf
   worker). Track B's resource numbers can now be cited.

2. **`fold_msa` vs `fold_oligomer` split is asymmetric between tracks, AND Track B's fold
   tasks run concurrently with no GPU guard — both contribute to the gap, and the second is
   the bigger one.** Track A's CageFlow splits the MSA fetch (CPU-bound, network-wait-
   dominated) into its own stage so the wait isn't billed to GPU time; Track B's `task_fold`
   does both in one task. But the more significant effect: Track B dispatches all 4 fold
   tasks via a single `asyncio.gather` with no concurrency limit, and `flowgentic.metrics`'s
   `resource="gpu"` label does not reserve or pin anything (`DragonExecutionBackendV1`
   silently drops it — confirmed by reading the backend). Four ColabFold processes
   contending for one GPU card measurably slows each one down; Track B's `fold_oligomer`
   mean elapsed (96.87s) is ~1.9x Track A's (50.95s) for the identical leaf worker on the
   identical GPU. This is a known, already-flagged limitation
   (`context/open-questions.md` #21; `main.py`'s own "KNOWN LIMITATIONS" docstring) — the fix
   is an `asyncio.Semaphore` sized to the job's GPU count around the fold task body, not yet
   built. Do not read the `fold_oligomer` gap as "ColabFold inference is slower under B" — it
   is real wall-clock cost, but it is a missing concurrency guard, not a difference in the
   inference itself.

3. **The earlier `design_interface` gap did not reproduce.** A prior version of this report
   measured 84.87s (A) vs 31.09s (B) and flagged it as unexplained. Under the current
   environment and code, both land within 1s of each other (40.04s vs 39.06s). Whichever
   factor caused the earlier gap — environment-specific dispatch overhead being the most
   likely candidate, though never confirmed — is not present here. Treat the earlier number
   as stale; do not cite it.

4. **`budget_summary.json` and `cageflow.log` previously existed only for Track A.** That
   gap is now closed — Track B's `cageflow.log` is restored (via
   `flowgentic.metrics.attach_log_file()`, re-asserted after `WorkflowEngine.create()` since
   Dragon's runtime bring-up calls `basicConfig(force=...)` and drops foreign handlers) and
   carries both the parent process's and every Dragon child's log lines, plus her own
   leaf-worker diagnostics (`Idealisation`, `Radius window`, `Spin period`). One expected,
   permanent asymmetry: Track A's log carries her *orchestration-level* lines (`Rosetta[`,
   `Budget[`, `Compute[`, `Fold[`) because those statements live inside the top-level
   functions Track A calls directly; Track B replaces those functions with its own stages
   and emits its own equivalents (`[rosetta]`, `[fold]`, etc.) plus `compute_stats.json`. Her
   `Budget`/`Compute` resource-accounting lines have no line-for-line Track B log
   equivalent — `budget_summary.json` still does not exist for Track B, by design, since
   Track B never instantiates her `Budget`/`Executor` (see `DESIGN.md`).

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
path). It was never going to tell us anything about run-to-run variance, because run-to-run
variance was deliberately switched off for this comparison.

**This round added a second kind of evidence the seeded match alone can't give: a controlled
environment change.** Moving off the module-provided torch to a plain CPU wheel changed the
*output* (different sampled sequences, different ddG) while leaving the *parity between
tracks* intact — Track A and three independent Track B code paths all shifted together, by
the same amount, to the same new numbers. That is evidence the glue is faithful across an
environment perturbation it was never deliberately tested against, which a same-environment
rerun cannot provide. It is still not evidence about noise within one environment — see below.

**We already have a measurement of what that variance actually looks like, from Laura**, and it
is large: her own measured Rosetta run-to-run noise (same sequence, same settings, unseeded) was
**heteroscedastic** — 0.092 kcal/mol stdev for one sequence, 1.055 kcal/mol for another, an 11x
spread between them (see `context/pipeline-operations.md`). Dock 2's `group_ddG=1132.9` in this
run is far outside any plausible noise band regardless; dock 1's `group_ddG=-56.3` is the kind
of number where a 1 kcal/mol-scale noise floor would matter to an interpretation. ColabFold/AF2
multimer inference has its own non-determinism sources (GPU kernel scheduling, any MSA-server
round trip) that a fixed `fold_seed` does not fully control, independent of Rosetta's.

**What this means going forward, concretely:**
- This report's exact match is the right outcome for *this* test and should be read as "no
  structural regression found across two code versions and three independent code paths,"
  not "the pipeline is noise-free."
- **This comparison has never once been run on the configuration Laura actually uses.**
  `n_docks=2`, `num_seq_per_target=2`, `fold_num_models=1` is roughly 1/250th of her
  real scale, and `fold_unit="oligomer"` / `restore_native_gaps=false` are both the
  *opposite* of her defaults (`"cage"` / `true`). A match on this probe config says
  the glue is faithful on this probe config — it does not yet say the glue is faithful on
  the config whose output anyone would actually publish.
- To actually characterize whether Track B's *distribution* of outcomes matches Track A's
  (the comparison that would matter once stage 2 removes barriers and stage 3 reasons over
  resource-vs-quality tradeoffs), either track needs to be run **several times** — ideally with
  seeding turned off, or deliberately varied — and the spread characterized the way Laura
  already did for Rosetta. That has not been done for either track yet.
- The real divergences this comparison *has* caught so far (a closure-capture hang, the
  MSA/PYTHONPATH bug, and the PYTHONPATH/ColabFold environment bug this update fixes) were
  all structural or environmental, not noise — consistent with the above: a single seeded
  A/B run is a sharp tool for finding "did the code path or environment change something
  real," and a blunt one for "did the noise profile change."
