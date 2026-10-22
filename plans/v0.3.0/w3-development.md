# W3: Release branch and PR B

W3 makes `main` green, installable and honest about its dependencies, then produces the evidence for D1. All of it lands in one PR, PR B, from branch `release/v0.3.0` cut from `main` after W1.

Gates: G1 (push the branch, open or update PR B), G2 (merge PR B). Decisions: D9 before R3, D8 before R5, D6 and D7 before PR B merges.

Rules for every requirement below:

- One commit per requirement, in order R0, R1, R2, R3, R4, R4b, R5, R6, R7.
- `uv run --extra dev pytest -q` stays green after every commit.
- PR B's body carries the Checks section (README, PR checks) and every done-check's output.

## R0 Plan files

- [x] Make the first commit on `release/v0.3.0` add `plans/v0.3.0/` and `CLAUDE.md`. Until then they stay untracked in Matteo's checkout at `/Users/mturilli/github/stride/flowgentic`; copy them from there when working in another worktree.

    Done-check: `git log --format=%s --reverse main..release/v0.3.0 | head -1` names the plan files' commit, and `git status --short plans CLAUDE.md` prints nothing.

## R1 Formatting

- [x] Run `uv run --extra dev ruff format` on the 5 flagged files: `src/flowgentic/agent.py`, `src/flowgentic/langGraph/execution_wrappers.py`, `src/flowgentic/langGraph/main.py`, `src/flowgentic/settings/extract_settings.py`, `src/flowgentic/utils/telemetry/report_generator.py`. Add Ruff `extend-exclude` entries for `.uv-cache`, `*.egg-info`, `build` and `site`.

    Done-check: `uv run --extra dev ruff format --check .` reports nothing to reformat.

## R2 Packaging metadata

- [x] In `pyproject.toml`:
    - Set `version = "0.3.0"`. Delete `[project.scripts]`: its target `flowgentic:main` does not exist.
    - Replace both git-URL dependencies with releases: `radical-asyncflow==0.5.0` and `academy-py==0.3.0`. Academy stays a core dependency because the API reference imports `flowgentic.academy`.
    - Declare `pyyaml`, which `src/flowgentic/settings/extract_settings.py` imports. Remove `langchain-community` and `langchain-mcp-adapters`, which nothing imports. Move `python-dotenv`, used only by examples, to an `examples` extra.
    - Cap `langgraph<1.0` and `langchain-core<1.0`: the adapter is tested on 0.6.11 and 0.3.79 only.
    - Raise `python-json-logger` from `>=2.0.0` to the first release that ships `pythonjsonlogger.json` (3.1.0 per the PR 101 review; confirm in its changelog). `src/flowgentic/utils/logger/logger.py` and `colorfulFormatter.py` import that module, so a pip install that resolves 2.x cannot import Flowgentic.
- [x] Regenerate the lock with `uv lock`. List every package whose locked version changed in PR B's body.
    - If the `uv.lock` diff touches packages R2 does not name: restore the lock and upgrade only R2's packages with `uv lock --upgrade-package <package>`.
    - If tests fail after the relock: compare the PyPI `radical-asyncflow` 0.5.0 wheel with commit `897faac`, which the current lock pins. Pinning to a git commit is the last resort; it makes v0.3.0 GitHub-only (D2).

    Done-check: run `uv build`; then, in a fresh venv outside the repo, `uv pip install dist/flowgentic-0.3.0-py3-none-any.whl` succeeds and `python -c "import flowgentic, flowgentic.agent, flowgentic.langGraph.main"` runs. The wheel's `METADATA` contains no `@ git+` line.

## R3 CI

- [ ] In `.github/workflows/continuous_integration.yml`, run the format check with the locked Ruff through `uv run`, replacing `astral-sh/ruff-action`.
- [ ] Add a docs job: `uv sync --frozen --extra dev`, then `uv run mkdocs build --strict`. Make `.github/workflows/docs-deployment.yml` build the same way, replacing `pip install -e .`.
- [ ] Test matrix: Python 3.10 and 3.11, plus 3.12 if D7 says so. The test step runs `uv run pytest` without the matrix interpreter or the dev extra (PR 101 review); pass both, as `uv run --python ${{ matrix.python-version }} --extra dev pytest …`, so each matrix job tests its own Python.
- [ ] If D9 places them in PR B, fix the 7 strict-build warnings here (the list is in w5-docs.md, Removals and fixes).

    Done-check: every CI job on PR B is green, including bandit, which has not run since August.

## R4 Demo launchers

- [x] `examples/ai-hpc-coupling/run_demo.sh`, `run_radical_demo.sh`, `run_agentic_demo.sh` and `run_augmented_demo.sh` use the installed AsyncFlow by default and prepend `ASYNCFLOW_SRC` to `PYTHONPATH` only when it is set. Today they exit with "AsyncFlow source not found" when `../../radical/radical.asyncflow/src` is missing.
- [x] `--controller adr` still requires `ADR_SRC`, because ADR is unreleased. Apply D6: support ADR mode at a stated ADR revision, or document it as unsupported.

- [x] Remove the committed `examples/ai-hpc-coupling/agentic_demo_results/` and add `agentic_demo_results/` to `examples/ai-hpc-coupling/.gitignore`, which already ignores the other three demos' result folders (PR 101 review). First check that no test, doc or README links into it.

    Done-check: in a fresh clone with no sibling repositories, `make examples-ai-hpc-deterministic` and `make examples-ai-hpc-agentic` both succeed, and `git status --short` is clean afterwards.

## R4b Candidate bounds

Both arms of R5 share this policy code, so it is corrected before the experiment runs. Found by the PR 101 review and confirmed at `dd27bd8`.

- [ ] In `examples/ai-hpc-coupling/agent_contracts.py`, make the candidate policy hold its hard bounds:
    - `enforce_candidate_policy` rounds candidates to 6 decimals, then `_candidate_batch_is_valid` compares them with the exact bounds, so a proposal at a bound can be rejected for rounding alone.
    - The last-resort batch from `evenly_spaced` is returned without validation, so a rounded endpoint can fall up to 5e-7 outside `[lower_bound, upper_bound]`.
- [ ] Add unit tests to `tests/unit/test_agentic_demo.py`: a proposal within rounding of a bound is accepted, and every returned candidate, fallback or last resort, lies within `[lower_bound, upper_bound]`.

    Done-check: the new tests fail on the unfixed code and pass after the fix, and the full suite stays green.

## R5 Substitution experiment

R5 runs the same agentic application with and without Flowgentic and reports what the adapter supplies. Its report is the evidence for D1.

- [ ] Add `examples/ai-hpc-coupling/direct_agentic_application.py`: `agentic_application.py` with every `flowgentic` decorator and `LangraphIntegration` replaced by direct AsyncFlow calls. The LangGraph graph stays unchanged.
- [ ] Add `--integration {flowgentic,direct}` to `examples/ai-hpc-coupling/agentic_campaign.py`, so both versions share decisions, budget, resources, retry policy, service lifetime and stopping rules. Its Flowgentic touchpoints today: the `LangraphIntegration(backend=backends)` context, which creates and shuts down the `WorkflowEngine`; `integration.flow`; and `integration.agent_introspector.generate_report(...)`.
- [ ] Add `tests/integration/test_substitution.py`, modelled on `tests/integration/test_ai_hpc_agentic_demo.py`. It runs both versions in rehearsal mode (`--agent-mode rehearsal`) with identical arguments and failure injection on, then compares their `campaign_summary.json`.
    - If decisions or results differ, the port has a bug: fix it until decisions and results match, then judge the other criteria.
- [ ] Write the report as `docs/evaluation/substitution.md`. Its table has one row per criterion:
    1. Results and scientific state transitions.
    2. Task and invocation identity.
    3. Failure, retry and cancellation.
    4. Service reuse and cleanup.
    5. Resource and token accounting.
    6. Application code and maintenance. This row also gives line counts of application-specific code and the dependency difference.

    Each row gives a verdict (adapter supplies it, both supply it, both lack it, or not tested) and its evidence (a test name or a log path). A capability counts for the adapter only when the adapter demonstrably supplies it; a capability both versions lack is an unmet requirement for the component that should own it. Token accounting is "not tested" unless Matteo provides a configured live environment; handle no credentials.

    Done-check: the test passes, every row has a verdict and evidence, and PR B's body links the report. Matteo then takes D1.

### Amendments A1–A4

Found by reading `demo/radical` at `dd27bd8` and AsyncFlow at `897faac`; nothing was executed. Apply each one D8 accepts.

- **A1 Retry ownership.** AsyncFlow 0.5.0 has no per-task retry or timeout, so the direct version writes its own. Flowgentic also wraps every call, graph nodes included, in its default 3 attempts × 30 s, and a node retry re-runs that node's simulations. The port reproduces only the explicit simulation policy, `SIMULATION_RETRY` in `examples/ai-hpc-coupling/demo_support.py` (2 attempts, 5 s); row 3 reports the implicit defaults.
- **A2 Observability row.** Flowgentic's telemetry code (`src/flowgentic/utils/telemetry/`, 1,005 lines) writes `execution_summary.md`, and no row covers it. Add row 7, observability and reporting, compared against AsyncFlow 0.5.0's OpenTelemetry workflow spans, which it emits when telemetry is active. The done-check then requires all seven rows.
- **A3 Unexercised features.** The demo calls its four tools from node code with `.ainvoke`, so the LLM never selects a tool; memory, handoffs and the mutable graph are unused. The report lists these as not tested, so they read as unevaluated.
- **A4 LangGraph 1.x cost.** The roadmap counts the 1.x port as a maintenance obligation. Row 6 adds an estimate of what a 1.x port of the adapter would change, without doing the port.

## R6 Decision binding

- [ ] `tests/unit/test_agent_adapter.py` covers `bind_agent` (`src/flowgentic/agent.py`): structured output, identity and timing. Add any missing case.

    Done-check: PR B's body names the covering tests, and they pass.

## R7 Changelog

- [ ] Add `CHANGELOG.md` in Keep a Changelog format, with a 0.3.0 entry (Added, Changed, Removed, Fixed). Note that tags `v0.1.0` (`3e43c13c`, 23 Sep 2025) and `0.2.0` (`72e972d4`, 2 Oct 2025) predate the changelog.

    Done-check: every user-visible change in PR B appears in the entry.
