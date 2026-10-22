# W1: Merge demo/radical into main

`main` receives the July work unchanged through a merge commit that keeps `dd27bd8` and its 11 commits in history. Fixes come afterwards, in W3.

Gates: G3 (snapshot tag), G1 (PR), G2 (merge, per D3).

## Branches

| Branch | Tip | Disposition | Status |
| --- | --- | --- | --- |
| `demo/radical` | `dd27bd8b7e6e` | Merge into `main`; stays on origin until D5 | Pending |
| `main` | `6c63601c049f` | Keep active | Pending |
| `feat/cnio-naive-pipeline` | `29a6fa9c1ad2` | Keep active and untouched | Not part of W1 |

`main` is an ancestor of `demo/radical` (11 ahead, 0 behind), so the merge has no conflicts.

## Baseline at dd27bd8

Recorded on 2026-10-22, macOS arm64, from `git archive`. The merge PR body carries this table.

| Check | Result |
| --- | --- |
| `uv sync --frozen --extra dev` | Passes |
| `uv run --extra dev pytest -q` | 22 passed, 2 skipped, 8.5 s |
| `uv run --extra dev mkdocs build --strict` | Fails: 7 warnings, 3 missing annotations (griffe) and 4 broken links in `design_patterns/chatbot.md` and `features/memory.md` |
| `ruff format --check` | Flags 5 tracked files (listed in R1) |
| CI on `main` and `demo/radical` tips | Red: the format job fails, so CI skips the tests and bandit jobs |
| `uv build` | Builds sdist and wheel 0.1.0 |
| `flowgentic` console script | `ImportError`: `[project.scripts]` points at `flowgentic:main`, which does not exist |

## Steps

- [x] 1. **Snapshot tag (G3).**

    ```bash
    git tag -a snapshot/radical-demo-2026-08 dd27bd8b7e6edb2995038c9c1cc9b2fec3b7c7e9 -m "Demos shown to RADICAL, July 2026 presentation"
    git push origin refs/tags/snapshot/radical-demo-2026-08
    ```

    Done-check: `git ls-remote --tags origin 'snapshot/*'` prints `dd27bd8b7e6e…`.

- [x] 2. **Merge PR (G1).** Open a PR on `stride-research/flowgentic` with head `demo/radical` and base `main`. Its body carries the baseline table and states that CI's format job stays red until W3 lands.

    Done-check: GitHub reports 11 commits and no conflicts.

- [x] 3. **Merge (G2).** `main` protection: 1 approving review, code-owner reviews on with no `CODEOWNERS` file, pushes restricted to `javidsegura`, admins not enforced. Follow D3's answer for the approval path; when asking for G2 without an approving review, name Matteo's admin bypass. Merge with GitHub's "Create a merge commit" option, which keeps the 11 commit SHAs that the snapshot tag and the deck cite.

    Done-check: `git rev-parse origin/main^2` prints `dd27bd8b7e6e…`.

- [x] 4. **Pages deploy.** The push to `main` triggers `Deploy MkDocs` (`.github/workflows/docs-deployment.yml`), which publishes https://stride-research.github.io/flowgentic/. Its last success was on 26 Jan 2026.

    Done-check: the run for the merge commit succeeds and the site loads. On failure the previous site stays live: record the failure in the merge PR and fix it in R3.

- [x] 5. **Coverage baseline.** On the merged `main`, run `uv run --extra dev pytest -q --cov=flowgentic` and record the total here. PR B and PR C may not lower it.

    Coverage on `main` after W1: **56%** (TOTAL 1534 statements, 668 missed, 56.45%), at merge commit `0a840236270f`; 22 passed, 2 skipped; macOS arm64, Python 3.10.20, 2026-10-22.

- [x] 6. **Local cleanup.** Fast-forward local `main` to `origin/main`. Delete local `feat/cnio-naive-pipeline-backup`: it is local-only, at `dd27bd8`, and now contained in `main`.

    Done-check: `git branch --list` no longer shows `feat/cnio-naive-pipeline-backup`, and `git rev-parse main origin/main` prints the same SHA twice.
