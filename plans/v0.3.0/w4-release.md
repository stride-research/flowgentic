# W4: Release v0.3.0

v0.3.0 is tagged on `main` after PR B (W3) and PR C (W5) merge and W2 is done. It ships as an annotated tag, a GitHub release with the wheel and sdist attached, and the documentation site; it goes to PyPI only if D2 says yes.

Gates: G5 (tag and release), G6 (PyPI, only if D2 says yes).

Repository facts: tags `v0.1.0` and `0.2.0` exist, with no GitHub releases. The name `flowgentic` is unclaimed on PyPI; `radical-asyncflow` 0.5.0 and `academy-py` 0.3.0 are published there.

## Release notes

The notes file states:

- What v0.3.0 is: the consolidation snapshot of the July work, and the D1 outcome (maintenance mode, or adapter kept for v0.4.0).
- Supported composition: LangGraph to AsyncFlow on local backends, on the Python versions D7 chose.
- Tested environment, from `uv.lock`: LangGraph 0.6.11, langchain-core 0.3.79, AsyncFlow 0.5.0.
- Links: the documentation site, the substitution report, tag `snapshot/radical-demo-2026-08`, and the `archive/*` tags from W2.
- Known limitations: the list in README.md.

## Steps

- [ ] 1. **Pre-release checks** on the `main` commit to tag:
    - PR B and PR C are merged, W2 is done, and CI is green on that commit.
    - `uv sync --locked --all-extras` and `uv run --extra dev pytest -q` pass locally.
    - `uv build` produces the 0.3.0 wheel and sdist, and the wheel passes R2's clean-venv import check.
    - The `CHANGELOG.md` 0.3.0 entry carries the release date.
    - The docs site deployed from that commit shows v0.3.0 and the D1 status.

    Done-check: each item's command output or observation is recorded with the release notes draft.

- [ ] 2. **Tag and push (G5).**

    ```bash
    git tag -a v0.3.0 <commit> -m "Flowgentic 0.3.0"
    git push origin refs/tags/v0.3.0
    ```

    Done-check: `git ls-remote --tags origin v0.3.0` prints the tag, and its peeled SHA is the checked commit.

- [ ] 3. **Publish the GitHub release (G5).** `gh release create v0.3.0 dist/* --title "Flowgentic 0.3.0" --notes-file <notes>`

    Done-check: `gh release view v0.3.0` lists 2 assets, and the wheel installs from its release URL in a clean venv.

- [ ] 4. **PyPI (G6), only if D2 says yes.** The publish workflow runs through trusted publishing, which Matteo configures.

    Done-check: `pip install flowgentic==0.3.0` succeeds in a clean venv.

- [ ] 5. **Announce.** Draft the note to RADICAL and the deck update that links the snapshot tag and the release, for Matteo to send (G7). Mark step 6 of the roadmap done.

    Done-check: Matteo confirms the note went out.

## Rollback

- A problem found before anyone is told (wrong commit tagged, or an asset fails to install): delete the release and the tag (G5 again), fix, and tag again.
- After the release is announced, tags stay where they are: fix forward as v0.3.1.
