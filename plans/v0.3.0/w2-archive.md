# W2: Archive inactive branches

Each of the 15 Archive branches becomes an annotated tag `archive/<branch>` on its tip, then its remote branch is deleted. The 4 Delete branches have no commits outside `main`, so they are deleted without a tag. W2 starts after W1 and runs alongside W3.

Gates: G7 (author messages), G3 (tags), G4 (close PRs, delete branches).

## Branches

Snapshot taken 22 Oct 2026 after `git fetch --prune origin`. "Ahead" is commits ahead of `origin/main` before W1.

| Branch | Tip | Author | Ahead | PR | Disposition | Status |
| --- | --- | --- | --- | --- | --- | --- |
| `hotfix/benchmarking_redesign` | `45268b5a73e2` | Matteo Turilli | 53 | #90 | Archive | Pending |
| `hotfix/benchmarking-engine-and-backends` | `44d5a4132ee7` | catebros | 55 | #96 | Archive | Pending |
| `experiment/exp3-benchmarking-hotfix` | `8bf7270425d6` | catebros | 59 | | Archive | Pending |
| `refactor/decoupling_tests` | `0940607b41ca` | javidominguezsegura | 56 | #95 | Archive | Pending |
| `feat/cluster_docs` | `feecf47f54b4` | Javier Dominguez | 39 | | Archive | Pending |
| `experiment1a` | `613b536586c8` | dianacord | 44 | #94 | Archive | Pending |
| `coordination_overhead_plot` | `5c0905438b68` | diechi09 | 22 | | Archive | Pending |
| `feat/abstractions_redesign` | `1ec100eaeb10` | javidominguezsegura | 9 | | Archive | Pending |
| `refactor/worload_old_interface` | `9a1d45b02d87` | dianacord | 11 | | Archive | Pending |
| `feat/benchmarking` | `4289a01d9e25` | javidominguezsegura | 1 | | Archive | Pending |
| `feature/telemetry` | `7ff024df1ed1` | dianacord | 7 | | Archive | Pending |
| `feature/distributed-memory` | `40fffaf3f861` | Yousef Amirghofran | 4 | | Archive | Pending |
| `feature/mcp` | `73333a4a7652` | dianacord | 2 | | Archive | Pending |
| `feature/observe-integration` | `0b121d045415` | Yousef Amirghofran | 6 | | Archive | Pending |
| `example/chemcrow` | `1972bcc0c656` | Yousef Amirghofran | 2 | | Archive | Pending |
| `hotfix/deafult-settings` | `db3fbe7066b2` | Yousef Amirghofran | 0 | | Delete | Pending |
| `docs/memory` | `b94f54a11baf` | Yousef Amirghofran | 0 | | Delete | Pending |
| `feature/memory` | `954675891d35` | Yousef Amirghofran | 0 | | Delete | Pending |
| `features/supervisor` | `d9c4911f0557` | javidominguezsegura | 0 | | Delete | Pending |

## Steps

- [ ] 1. **Verify tips.** After `git fetch --prune origin`, compare every row with `git rev-parse --short=12 origin/<branch>`.

    Done-check: all 19 match. A row with drift is left alone (README, working rule 1); the others continue.

- [ ] 2. **Tell the authors (G7).** Draft one short message per author (javidsegura, catebros, dianacord, Yousef Amirghofran, diechi09) listing their branches, the tag names and the PRs to be closed. Matteo sends or approves them.

    Done-check: Matteo confirms the messages went out, or waives them.

- [ ] 3. **Tag and push (G3).**

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

- [ ] 4. **Close PRs #90, #94, #95 and #96 (G4).** Comment on each with the branch's archive tag and that its commits stay reachable through that tag, then close it.

    Done-check: `gh pr list --state open` lists none of the four.

- [ ] 5. **Delete the 19 remote branches (G4)**, each with `git push origin --delete <branch>`. Delete an Archive branch only after step 3's done-check shows its tag on origin. Mark each row Done.

    Done-check: `git ls-remote --heads origin` lists only `main`, `demo/radical`, `feat/cnio-naive-pipeline` and any open work branch (such as `release/v0.3.0`).

- [ ] 6. **Local cleanup.** Delete local `hotfix/benchmarking_redesign`, then run `git fetch --prune origin`.

    Done-check: `git branch --list` shows none of the 19 branches.

## Restoring and links

- Restore an archived branch: `git push origin 'archive/<branch>^{commit}:refs/heads/<branch>'`.
- Links that must keep working: CageController cites commit `2e2f8f37` on the kept CNIO branch, and the deck cites `tree/demo/radical`, which stays until D5.
