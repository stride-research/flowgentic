# W2: Archive inactive branches

Each of the 15 Archive branches becomes an annotated tag `archive/<branch>` on its tip, then its remote branch is deleted. The 4 Delete branches have no commits outside `main`, so they are deleted without a tag. W2 starts after W1 and runs alongside W3.

Gates: G7 (author messages), G3 (tags), G4 (close PRs, delete branches).

## Branches

Snapshot taken 22 Oct 2026 after `git fetch --prune origin`. "Ahead" is commits ahead of `origin/main` before W1.

| Branch | Tip | Author | Ahead | PR | Disposition | Status |
| --- | --- | --- | --- | --- | --- | --- |
| `hotfix/benchmarking_redesign` | `45268b5a73e2` | Matteo Turilli | 53 | #90 | Archive | Done |
| `hotfix/benchmarking-engine-and-backends` | `44d5a4132ee7` | catebros | 55 | #96 | Archive | Done |
| `experiment/exp3-benchmarking-hotfix` | `8bf7270425d6` | catebros | 59 | | Archive | Done |
| `refactor/decoupling_tests` | `0940607b41ca` | javidominguezsegura | 56 | #95 | Archive | Done |
| `feat/cluster_docs` | `feecf47f54b4` | Javier Dominguez | 39 | | Archive | Done |
| `experiment1a` | `613b536586c8` | dianacord | 44 | #94 | Archive | Done |
| `coordination_overhead_plot` | `5c0905438b68` | diechi09 | 22 | | Archive | Done |
| `feat/abstractions_redesign` | `1ec100eaeb10` | javidominguezsegura | 9 | | Archive | Done |
| `refactor/worload_old_interface` | `9a1d45b02d87` | dianacord | 11 | | Archive | Done |
| `feat/benchmarking` | `4289a01d9e25` | javidominguezsegura | 1 | | Archive | Done |
| `feature/telemetry` | `7ff024df1ed1` | dianacord | 7 | | Archive | Done |
| `feature/distributed-memory` | `40fffaf3f861` | Yousef Amirghofran | 4 | | Archive | Done |
| `feature/mcp` | `73333a4a7652` | dianacord | 2 | | Archive | Done |
| `feature/observe-integration` | `0b121d045415` | Yousef Amirghofran | 6 | | Archive | Done |
| `example/chemcrow` | `1972bcc0c656` | Yousef Amirghofran | 2 | | Archive | Done |
| `hotfix/deafult-settings` | `db3fbe7066b2` | Yousef Amirghofran | 0 | | Delete | Done |
| `docs/memory` | `b94f54a11baf` | Yousef Amirghofran | 0 | | Delete | Done |
| `feature/memory` | `954675891d35` | Yousef Amirghofran | 0 | | Delete | Done |
| `features/supervisor` | `d9c4911f0557` | javidominguezsegura | 0 | | Delete | Done |

## Steps

- [x] 1. **Verify tips.** After `git fetch --prune origin`, compare every row with `git rev-parse --short=12 origin/<branch>`.

    Done-check: all 19 match. A row with drift is left alone (README, working rule 1); the others continue.

    Result (2026-10-22, W2 session): `git fetch --prune origin` exit 0; `git rev-parse --short=12 origin/<branch>` equals the Tip column for all 19 rows, no drift. The 4 Delete branches are ancestors of `origin/main` (0 commits ahead); ahead counts against pre-W1 `main` (`6c63601c049f`) equal the Ahead column for all 19. Open PRs: #90, #94, #95, #96, heads matching the table. PR #90 was opened by javidsegura.

- [x] 2. **Tell the authors (G7).** Draft one short message per author (javidsegura, catebros, dianacord, Yousef Amirghofran, diechi09) listing their branches, the tag names and the PRs to be closed. Matteo sends or approves them.

    Done-check: Matteo confirms the messages went out, or waives them.

    Result (2026-10-22): five drafts written (one per author: branches, tag names, PRs to close, restore command); Matteo waived the messages in chat (G7 answer "Waive the messages"). After steps 3–6, one general message to all five authors replaced the per-author drafts: what was archived, deleted and closed; each author's branches and PRs; the tag naming; how to fetch, check out and restore an archived branch; and an offer to restore a branch or reopen a PR. Matteo confirmed in chat that he sent it (2026-10-22).

- [x] 3. **Tag and push (G3).**

    ```bash
    for b in hotfix/benchmarking_redesign hotfix/benchmarking-engine-and-backends \
        experiment/exp3-benchmarking-hotfix refactor/decoupling_tests feat/cluster_docs \
        experiment1a coordination_overhead_plot feat/abstractions_redesign \
        refactor/worload_old_interface feat/benchmarking feature/telemetry \
        feature/distributed-memory feature/mcp feature/observe-integration example/chemcrow; do
      git tag -a "archive/$b" "origin/$b" -m "Archived $(date +%F): inactive at the v0.3.0 consolidation"
    done
    git push origin 'refs/tags/archive/*'
    ```

    Done-check: `git ls-remote --tags origin 'archive/*'` shows 15 peeled (`^{}`) SHAs equal to the table's tips.

    Result (2026-10-22): 15 signed annotated tags created locally (message "Archived 2026-10-22: inactive at the v0.3.0 consolidation"), pushed on Matteo's G3 yes (`git push origin 'refs/tags/archive/*'` exit 0, 15 new tags). `git ls-remote --tags origin 'archive/*'` lists 15 tag objects and 15 peeled `^{}` SHAs; each peeled SHA's first 12 characters equals its row's Tip.

    | Tag | Tag object | Peeled (`^{}`) |
    | --- | --- | --- |
    | `archive/hotfix/benchmarking_redesign` | `3123ff1a0703` | `45268b5a73e2` |
    | `archive/hotfix/benchmarking-engine-and-backends` | `1c0086c797a4` | `44d5a4132ee7` |
    | `archive/experiment/exp3-benchmarking-hotfix` | `36d60182b497` | `8bf7270425d6` |
    | `archive/refactor/decoupling_tests` | `2130fca3eb1e` | `0940607b41ca` |
    | `archive/feat/cluster_docs` | `770fcfd14752` | `feecf47f54b4` |
    | `archive/experiment1a` | `870704cffeaa` | `613b536586c8` |
    | `archive/coordination_overhead_plot` | `9e2143b9c63d` | `5c0905438b68` |
    | `archive/feat/abstractions_redesign` | `b8d0c3cf675f` | `1ec100eaeb10` |
    | `archive/refactor/worload_old_interface` | `b6357aa94f43` | `9a1d45b02d87` |
    | `archive/feat/benchmarking` | `7c2f794e6b6a` | `4289a01d9e25` |
    | `archive/feature/telemetry` | `711950934610` | `7ff024df1ed1` |
    | `archive/feature/distributed-memory` | `29508ad554ad` | `40fffaf3f861` |
    | `archive/feature/mcp` | `c4b6082a2def` | `73333a4a7652` |
    | `archive/feature/observe-integration` | `ac7178ae3981` | `0b121d045415` |
    | `archive/example/chemcrow` | `14faeb88b035` | `1972bcc0c656` |

- [x] 4. **Close PRs #90, #94, #95 and #96 (G4).** Comment on each with the branch's archive tag and that its commits stay reachable through that tag, then close it.

    Done-check: `gh pr list --state open` lists none of the four.

    Result (2026-10-22): on Matteo's G4 yes, tips re-checked (no drift), then `gh pr close <n> --comment …` for each, exit 0. Comment: "Closing as part of the Flowgentic v0.3.0 consolidation. This branch is archived as the annotated tag `archive/<branch>` (tip `<sha12>`), and all of its commits stay reachable through that tag. To restore the branch: `git push origin 'archive/<branch>^{commit}:refs/heads/<branch>'`". Closed at 08:07:56Z (#90), 08:07:58Z (#94), 08:08:01Z (#95), 08:08:03Z (#96). `gh pr list --state open` returns `[]`.

- [x] 5. **Delete the 19 remote branches (G4)**, each with `git push origin --delete <branch>`. Delete an Archive branch only after step 3's done-check shows its tag on origin. Mark each row Done.

    Done-check: `git ls-remote --heads origin` lists only `main`, `demo/radical`, `feat/cnio-naive-pipeline` and any open work branch (such as `release/v0.3.0`).

    Result (2026-10-22): on Matteo's G4 yes, each branch was deleted only after a fresh `git ls-remote` showed its remote head equal to the Tip and, for the 15 Archive rows, `archive/<branch>^{}` on origin equal to the Tip (the 4 Delete rows were re-checked as ancestors of `origin/main`). All 19 `git push origin --delete <branch>` exit 0; all rows marked Done. `git ls-remote --heads origin` now lists only `demo/radical` (`dd27bd8b7e6e`), `feat/cnio-naive-pipeline` (`29a6fa9c1ad2`) and `main` (`0a840236270f`); `release/v0.3.0` not pushed yet. 15 `archive/*` tags remain on origin.

- [x] 6. **Local cleanup.** Delete local `hotfix/benchmarking_redesign`, then run `git fetch --prune origin`.

    Done-check: `git branch --list` shows none of the 19 branches.

    Result (2026-10-22): local `hotfix/benchmarking_redesign` was at `45268b5a73e2`, equal to `archive/hotfix/benchmarking_redesign^{commit}`, so `git branch -D` lost nothing. `git fetch --prune origin` exit 0. `git branch --list` shows `demo/radical`, `feat/cnio-naive-pipeline`, `main` and `release/v0.3.0` (W3's worktree); `git branch -r` shows `origin/demo/radical`, `origin/feat/cnio-naive-pipeline`, `origin/main`. No local or remote-tracking ref remains for any of the 19 branches.

## Restoring and links

- Restore an archived branch: `git push origin 'archive/<branch>^{commit}:refs/heads/<branch>'`.
- Links that must keep working: CageController cites commit `2e2f8f37` on the kept CNIO branch, and the deck cited `tree/demo/radical` until D5. D5 is done (2026-10-22): the deck links `snapshot/radical-demo-2026-08`, and `demo/radical` was deleted.
