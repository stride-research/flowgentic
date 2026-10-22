# W4: Release v0.3.0

v0.3.0 is tagged on `main` after PR B (W3) and PR C (W5) merge and W2 is done. It ships as an annotated tag, a GitHub release with the wheel and sdist attached, and the documentation site; it goes to PyPI only if D2 says yes.

Gates: G5 (tag and release), G6 (PyPI, only if D2 says yes).

Repository facts: tags `v0.1.0` and `0.2.0` exist, with no GitHub releases. The name `flowgentic` is unclaimed on PyPI; `radical-asyncflow` 0.5.0 and `academy-py` 0.3.0 are published there.

## Release notes

The notes file states:

- What v0.3.0 is: the consolidation snapshot of the July work, and the D1 outcome (maintenance mode, or adapter kept for v0.4.0).
- Supported composition: LangGraph to AsyncFlow on local backends, on the Python versions D7 chose.
- Tested environment, from `uv.lock`: LangGraph 1.2.16, langchain-core 1.6.13, langchain 1.4.7, AsyncFlow 0.6.0 (R8, D10).
- Links: the documentation site, the substitution report, tag `snapshot/radical-demo-2026-08`, and the `archive/*` tags from W2.
- Known limitations: the list in README.md.

## Steps

- [x] 1. **Pre-release checks** on the `main` commit to tag:
    - PR B and PR C are merged, W2 is done, and CI is green on that commit.
    - `uv sync --locked --all-extras` and `uv run --extra dev pytest -q` pass locally.
    - `uv build` produces the 0.3.0 wheel and sdist, and the wheel passes R2's clean-venv import check.
    - The `CHANGELOG.md` 0.3.0 entry carries the release date, and `CITATION.cff` gets it as `date-released` (W5 added the file without one; `uvx cffconvert --validate` passes either way).
    - The docs site deployed from that commit shows v0.3.0 and the D1 status.

    Done-check: each item's command output or observation is recorded with the release notes draft.

    Result (2026-10-22): the commit to tag is `c196ef19`, the merge of the prep PR #105, which set the release date and recorded D2. PR #105's body holds the local check outputs.
    - PR B (#102) and PR C (#103) merged; W2 done. CI run 39817391455 on `c196ef19`: all 6 jobs success.
    - `uv sync --locked --all-extras` exits 0; `uv run --extra dev pytest -q`: 50 passed, 4 skipped.
    - `uv build` from a clean `git archive` of `c196ef19` gives the wheel (sha256 `a34e167e…`) and the sdist (sha256 `46089c8b…`). The wheel installs and imports in clean venvs on Python 3.10, 3.11 and 3.12, and its `METADATA` has no `@ git+` line.
    - `CHANGELOG.md` 0.3.0 and `CITATION.cff` `date-released` are 2026-10-22; `uvx cffconvert --validate` passes.
    - Deploy MkDocs run 39817391592 for `c196ef19` succeeded; the site says "This documentation describes Flowgentic 0.3.0", and the status page says the adapter is frozen in maintenance mode.
    - PR #104 (v0.3.1) is open and none of its commits is in `c196ef19`.

- [x] 2. **Tag and push (G5).**

    ```bash
    git tag -a v0.3.0 <commit> -m "Flowgentic 0.3.0"
    git push origin refs/tags/v0.3.0
    ```

    Done-check: `git ls-remote --tags origin v0.3.0` prints the tag, and its peeled SHA is the checked commit.

    Result (2026-10-22): `refs/tags/v0.3.0` is the SSH-signed tag object `c85b2ec1`, and `v0.3.0^{}` is `c196ef194b92`.

- [x] 3. **Publish the GitHub release (G5).** `gh release create v0.3.0 dist/* --title "Flowgentic 0.3.0" --notes-file <notes>`

    Done-check: `gh release view v0.3.0` lists 2 assets, and the wheel installs from its release URL in a clean venv.

    Result (2026-10-22): https://github.com/stride-research/flowgentic/releases/tag/v0.3.0, published 17:25:40 UTC and marked Latest, lists 2 assets, `flowgentic-0.3.0-py3-none-any.whl` and `flowgentic-0.3.0.tar.gz`, whose GitHub digests match the local build. Installed from its release URL, the wheel imports in clean venvs on Python 3.10, 3.11 and 3.12 and resolves LangGraph 1.2.16 and AsyncFlow 0.6.0.

- [x] 4. **PyPI (G6), only if D2 says yes.** The publish workflow runs through trusted publishing, which Matteo configures.

    Done-check: `pip install flowgentic==0.3.0` succeeds in a clean venv.

    Result: skipped. D2 = GitHub release only (Matteo, 2026-10-22).

- [ ] 5. **Announce.** Draft the note to RADICAL and the deck update that links the snapshot tag and the release, for Matteo to send (G7). Mark step 6 of the roadmap done.

    Done-check: Matteo confirms the note went out.

    Drafted (2026-10-22) for Matteo to send: the RADICAL note, and the deck update, which replaces the deck's `tree/demo/radical` link with `snapshot/radical-demo-2026-08` and adds the release. The roadmap's step 6 is his to mark done.

## Rollback

- A problem found before anyone is told (wrong commit tagged, or an asset fails to install): delete the release and the tag (G5 again), fix, and tag again.
- After the release is announced, tags stay where they are: fix forward as v0.3.1.
