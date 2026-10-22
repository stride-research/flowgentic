# Flowgentic v0.3.0: consolidation release

v0.3.0 preserves the July 2026 work as a supported, documented snapshot: fixes and evidence, no new features. This plan merges `demo/radical` into `main`, archives the inactive branches, fixes packaging and CI, runs the substitution experiment, releases v0.3.0 and rebuilds the documentation site.

It implements steps 1, 2, 3 and 6 of the [roadmap](https://claude.ai/artifact/CcnX2dQqmBYaWZoxKnedoA). Roadmap steps 4 (CNIO code) and 5 (upstream requests) are outside it. Converted on 2026-10-22 from Matteo's [spec doc](https://claude.ai/artifact/92wRQLTezYHqjCAzrABMeo); these files are the working copy, so record progress, decisions and changes here.

## Working rules

1. Start every session with `git fetch --prune origin`. Before acting on a branch, compare `git rev-parse --short=12 origin/<branch>` with the tip in its workstream file. A different tip is **drift**: someone has pushed. Leave that branch alone, re-inventory, and ask Matteo.
2. Follow **Work order**. Each step ends in a **done-check**, a command or observation that proves the step finished. A step is done once its done-check output is in the PR description or the experiment report; then tick its box.
3. A step tagged with a gate (G1–G7) waits for Matteo's explicit yes in chat. One yes covers that one action.
4. Anything these files do not cover: stop and ask Matteo.

## Gates

| Gate | Action | Notes |
| --- | --- | --- |
| G1 | Push a work branch, or open or update a PR on `stride-research/flowgentic` | Asked once per working session. |
| G2 | Merge a PR into `main` | See W1 step 3 for branch protection and D3. |
| G3 | Push `archive/*` and `snapshot/*` tags | |
| G4 | Close PRs #90, #94, #95, #96; delete remote branches | Authors are told first (G7). |
| G5 | Push tag `v0.3.0`; publish or delete the GitHub release | |
| G6 | Publish to PyPI | Only if D2 says yes. Matteo configures the PyPI trusted publisher; handle no tokens. |
| G7 | Any message to someone other than Matteo | Draft it; Matteo sends or approves it. |

## Work order

1. W1 first.
2. After W1, run W2 and W3 side by side.
3. W3's R5 report goes to Matteo, who takes D1 with RADICAL. D1 = keep also starts a separate adapter spec for v0.4.0, outside this plan.
4. After D1 and PR B's merge, W5 (PR C). Its status pages state the D1 outcome.
5. After PR B, PR C and W2 are all done, W4. The release notes list W2's archive tags.
6. After W4, retire `demo/radical` per D5.

## Files

| File | Read it for |
| --- | --- |
| [w1-merge.md](w1-merge.md) | Snapshot tag, merging `demo/radical` into `main`, the baseline at `dd27bd8` |
| [w2-archive.md](w2-archive.md) | Tagging and deleting the 19 inactive branches, author messages, closing PRs |
| [w3-development.md](w3-development.md) | Branch `release/v0.3.0` and PR B: formatting, packaging, CI, launchers, the substitution experiment (R5), `bind_agent` tests, changelog |
| [w4-release.md](w4-release.md) | Tagging and publishing v0.3.0, release notes, rollback |
| [w5-docs.md](w5-docs.md) | PR C: documentation site, README, status page |

## Out of scope

- `feat/cnio-naive-pipeline` stays active, unmerged and unrewritten. Its tree removes `src/flowgentic/langGraph/`, so merging it would delete the adapter, and CageController and `cnio_usecase` cite its commits.
- Adapter improvements (supplied engine, cancellation, retry ownership, accounting) wait for D1. If the adapter is kept, they get their own spec and target v0.4.0. LangGraph 1.x moved into PR B by D10 (W3 R8).
- Import-time behaviour (DEBUG logging by default, `config.yml` lookup in the working directory) stays as it is and is documented under Known limitations.
- Execution beyond local backends, and any change to ADR or AuCaM.
- Issue #100 (DAG visualization) stays open.

## Open decisions

Matteo takes each decision. Bring the evidence and the recommendation, and record his answer in the Answer column before the step that needs it.

| ID | Decision | Options | Recommendation | Needed before | Answer |
| --- | --- | --- | --- | --- | --- |
| D1 | Keep or freeze the execution adapter | Freeze in maintenance mode; or keep for v0.4.0 with a named consumer | Follow the R5 report, with RADICAL | PR C | |
| D2 | Publish to PyPI | GitHub release only; or PyPI too | GitHub release only: a frozen package gains little from PyPI, and PyPI needs trusted-publisher setup | W4 | |
| D3 | Approval path into `main` | Javier reviews; or Matteo's admin bypass | Ask Javier first; bypass only if no review arrives | W1 step 3 | Javier reviews: Claude requests his review on the merge PR with `gh` (approved as a G7 message on 2026-10-22); admin bypass only if no review arrives, asked at G2. 2026-10-22, PR 101: CodeRabbit and Copilot reviewed it in place of Javier (both comment-only; 5 findings logged as W3 work, PR left unchanged), and Matteo approved G2 with his admin bypass. |
| D4 | Citation | Keep the 3 listed authors, or add contributors; with or without a Zenodo DOI | Add a `CITATION.cff` listing the contributors Matteo approves; Zenodo only if Matteo enables it | PR C | |
| D5 | When to delete `demo/radical` | After the deck links the snapshot tag; or keep it | Delete once the deck links `snapshot/radical-demo-2026-08` | After W4 | |
| D6 | ADR mode in the release | Supported at a stated ADR revision; or documented as unsupported | Unsupported, since ADR is unreleased | PR B | Documented as unsupported (Matteo, 2026-10-22, relayed from the parent session). ADR has no PyPI release or tags; `--controller adr` keeps requiring `ADR_SRC`. |
| D7 | Python versions | 3.10–3.11; or 3.10–3.12 | Add 3.12 if the lock resolves and CI is green | PR B | 3.10–3.12 (Matteo, 2026-10-22, relayed from the parent session). The locked suite passed locally on all three before CI. |
| D8 | R5 amendments A1–A4 (in w3-development.md) | Accept each, reject each | Accept all four | R5 | Accept all A1–A4 (Matteo, 2026-10-22, relayed from the parent session). |
| D9 | Where the 7 strict-build warning fixes land | PR B (R3); or PR C (W5) as the spec places them | PR B: R3's docs job must pass on PR B, and it fails while the warnings remain | R3 | PR B, in R3 (Matteo, 2026-10-22, relayed from the parent session). |
| D10 | Extend PR B before merge with R8 (AsyncFlow 0.6.0 and LangGraph 1.x) and R5b (offline LLM tool-selection and handoff test) | Add both to PR B; or leave both for v0.4.0 | Add both: R5 found the 1.x port small and the tool-selection and handoff paths untested | PR B merge | Yes (Matteo, 2026-10-22, relayed from the parent session): "have W3 add R8 to PR B (AsyncFlow 0.6.0 and LangGraph 1.x: relock, lift the <1.0 caps, move the 9 example calls to create_agent, drop the 2 unused imports, add a 1.x CI job, rerun R5 and update its versions) and R5b (an offline LLM tool-selection and handoff test with a scripted chat model, both versions)". |

## PR checks

Every PR body has a Checks section giving, for each row the PR requires, the command run and its result.

| Area | Command | Runs in | Required for |
| --- | --- | --- | --- |
| Unit suite (`bind_agent`, wrappers, introspection, memory) | `uv run --extra dev pytest -q tests/unit` | CI; locally after each commit | Every PR |
| Deterministic, augmented and agentic demos, offline | `uv run --extra dev pytest -q tests/integration` (LLM tests skip themselves) | CI | Every PR |
| With and without Flowgentic, offline | `uv run --extra dev pytest -q tests/integration/test_substitution.py` | CI | PR B; evidence for D1 |
| LLM examples, opt-in | `FLOWGENTIC_RUN_LLM_INTEGRATION=1` with credentials Matteo has configured | Locally, only on Matteo's request | Not required |
| Package | `uv build`, then a clean-venv install and import outside the repo (R2's done-check) | Locally | PR B; release |
| Demo launchers | `make examples-ai-hpc-deterministic` and `make examples-ai-hpc-agentic` in a fresh clone without sibling repositories | Locally | PR B |
| Documentation | `uv run --extra dev mkdocs build --strict` | CI docs job | PR B onward |
| Format and security | `uv run --extra dev ruff format --check .`; `bandit -r src/` | CI | Every PR |

Coverage: PR B and PR C keep coverage at or above the total recorded in W1 step 5.

## Known limitations

Single list for the W4 release notes and the W5 status page.

- DEBUG logging is on by default, and `config.yml` is looked up in the working directory at import.
- ADR mode needs an ADR source checkout (per D6).
- Not validated: use of a caller-supplied engine, any remote or HPC backend. Cancellation reaching in-flight AsyncFlow tasks is validated on local backends only (R5).
- Token accounting: the agentic demo records none, even in live mode (R5 report, row 5).
