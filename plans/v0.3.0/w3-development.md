# W3: Release branch and PR B

W3 makes `main` green, installable and honest about its dependencies, then produces the evidence for D1. All of it lands in one PR, PR B, from branch `release/v0.3.0` cut from `main` after W1.

Gates: G1 (push the branch, open or update PR B), G2 (merge PR B). Decisions: D9 before R3, D8 before R5, D6 and D7 before PR B merges. D10 (yes) adds R8 and R5b.

Rules for every requirement below:

- One commit per requirement, in order R0, R1, R2, R3, R4, R4b, R5, R6, R7; then R8 and R5b (D10), in that order, because R5b's test is written against the stack R8 sets.
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
    - Cap `langgraph<1.0` and `langchain-core<1.0`: the adapter is tested on 0.6.11 and 0.3.79 only. Superseded by R8 (D10), which lifts the caps and raises the floors to 1.0.
    - Raise `python-json-logger` from `>=2.0.0` to the first release that ships `pythonjsonlogger.json` (3.1.0 per the PR 101 review; confirm in its changelog). `src/flowgentic/utils/logger/logger.py` and `colorfulFormatter.py` import that module, so a pip install that resolves 2.x cannot import Flowgentic.
- [x] Regenerate the lock with `uv lock`. List every package whose locked version changed in PR B's body.
    - If the `uv.lock` diff touches packages R2 does not name: restore the lock and upgrade only R2's packages with `uv lock --upgrade-package <package>`.
    - If tests fail after the relock: compare the PyPI `radical-asyncflow` 0.5.0 wheel with commit `897faac`, which the current lock pins. Pinning to a git commit is the last resort; it makes v0.3.0 GitHub-only (D2).

    Done-check: run `uv build`; then, in a fresh venv outside the repo, `uv pip install dist/flowgentic-0.3.0-py3-none-any.whl` succeeds and `python -c "import flowgentic, flowgentic.agent, flowgentic.langGraph.main"` runs. The wheel's `METADATA` contains no `@ git+` line.

## R3 CI

- [x] In `.github/workflows/continuous_integration.yml`, run the format check with the locked Ruff through `uv run`, replacing `astral-sh/ruff-action`.
- [x] Add a docs job: `uv sync --frozen --extra dev`, then `uv run mkdocs build --strict`. Make `.github/workflows/docs-deployment.yml` build the same way, replacing `pip install -e .`.
- [x] Test matrix: Python 3.10 and 3.11, plus 3.12 if D7 says so. The test step runs `uv run pytest` without the matrix interpreter or the dev extra (PR 101 review); pass both, as `uv run --python ${{ matrix.python-version }} --extra dev pytest …`, so each matrix job tests its own Python.
- [x] If D9 places them in PR B, fix the 7 strict-build warnings here (the list is in w5-docs.md, Removals and fixes).

    Done-check: every CI job on PR B is green, including bandit, which has not run since August.

## R4 Demo launchers

- [x] `examples/ai-hpc-coupling/run_demo.sh`, `run_radical_demo.sh`, `run_agentic_demo.sh` and `run_augmented_demo.sh` use the installed AsyncFlow by default and prepend `ASYNCFLOW_SRC` to `PYTHONPATH` only when it is set. Today they exit with "AsyncFlow source not found" when `../../radical/radical.asyncflow/src` is missing.
- [x] `--controller adr` still requires `ADR_SRC`, because ADR is unreleased. Apply D6: support ADR mode at a stated ADR revision, or document it as unsupported.

- [x] Remove the committed `examples/ai-hpc-coupling/agentic_demo_results/` and add `agentic_demo_results/` to `examples/ai-hpc-coupling/.gitignore`, which already ignores the other three demos' result folders (PR 101 review). First check that no test, doc or README links into it.

    Done-check: in a fresh clone with no sibling repositories, `make examples-ai-hpc-deterministic` and `make examples-ai-hpc-agentic` both succeed, and `git status --short` is clean afterwards.

## R4b Candidate bounds

Both arms of R5 share this policy code, so it is corrected before the experiment runs. Found by the PR 101 review and confirmed at `dd27bd8`.

- [x] In `examples/ai-hpc-coupling/agent_contracts.py`, make the candidate policy hold its hard bounds:
    - `enforce_candidate_policy` rounds candidates to 6 decimals, then `_candidate_batch_is_valid` compares them with the exact bounds, so a proposal at a bound can be rejected for rounding alone.
    - The last-resort batch from `evenly_spaced` is returned without validation, so a rounded endpoint can fall up to 5e-7 outside `[lower_bound, upper_bound]`.
- [x] Add unit tests to `tests/unit/test_agentic_demo.py`: a proposal within rounding of a bound is accepted, and every returned candidate, fallback or last resort, lies within `[lower_bound, upper_bound]`.

    Done-check: the new tests fail on the unfixed code and pass after the fix, and the full suite stays green.

## R5 Substitution experiment

R5 runs the same agentic application with and without Flowgentic and reports what the adapter supplies. Its report is the evidence for D1.

- [x] Add `examples/ai-hpc-coupling/direct_agentic_application.py`: `agentic_application.py` with every `flowgentic` decorator and `LangraphIntegration` replaced by direct AsyncFlow calls. The LangGraph graph stays unchanged.
- [x] Add `--integration {flowgentic,direct}` to `examples/ai-hpc-coupling/agentic_campaign.py`, so both versions share decisions, budget, resources, retry policy, service lifetime and stopping rules. Its Flowgentic touchpoints today: the `LangraphIntegration(backend=backends)` context, which creates and shuts down the `WorkflowEngine`; `integration.flow`; and `integration.agent_introspector.generate_report(...)`.
- [x] Add `tests/integration/test_substitution.py`, modelled on `tests/integration/test_ai_hpc_agentic_demo.py`. It runs both versions in rehearsal mode (`--agent-mode rehearsal`) with identical arguments and failure injection on, then compares their `campaign_summary.json`.
    - If decisions or results differ, the port has a bug: fix it until decisions and results match, then judge the other criteria.
- [x] Write the report as `docs/evaluation/substitution.md`. Its table has one row per criterion:
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

- [x] `tests/unit/test_agent_adapter.py` covers `bind_agent` (`src/flowgentic/agent.py`): structured output, identity and timing. Add any missing case.

    Done-check: PR B's body names the covering tests, and they pass.

## R7 Changelog

- [x] Add `CHANGELOG.md` in Keep a Changelog format, with a 0.3.0 entry (Added, Changed, Removed, Fixed). Note that tags `v0.1.0` (`3e43c13c`, 23 Sep 2025) and `0.2.0` (`72e972d4`, 2 Oct 2025) predate the changelog.

    Done-check: every user-visible change in PR B appears in the entry.

## R8 AsyncFlow 0.6.0 and LangGraph 1.x

Added by D10. R5's probe found that the suite passes unchanged on LangGraph 1.x, and the parent session found the same on AsyncFlow 0.6.0. AsyncFlow 0.6.0 still has no per-task retry or timeout, and its spans still label every task with the default backend.

- [x] In `pyproject.toml`, pin `radical-asyncflow==0.6.0`. Replace the `<1.0` caps with `langgraph>=1.0,<2.0` and `langchain-core>=1.0,<2.0`, and add `langchain>=1.0,<2.0`, which `create_agent` needs. Raising the floors to 1.0 means no CI job is needed on a 0.6 lower bound; PR B's body says so. Relock with `uv lock --upgrade-package` for these packages only, and list every locked version change in PR B's body.
- [x] Remove the 2 unused `create_react_agent` imports in `src/flowgentic` (`langGraph/execution_wrappers.py`, `langGraph/utils/supervisor.py`). Move the 9 example calls to `langchain.agents.create_agent` (`prompt` becomes `system_prompt`). The docs snippets that show `create_react_agent` belong to W5's design-pattern audit.
- [x] Rename `ConcurrentExecutionBackend` to `LocalExecutionBackend` in the 9 example files that import it. AsyncFlow 0.1.0 exported it, but 0.3.0 and later do not; the examples, the 4 migrated ones among them, have failed at import since `172deb8f` (2026-08-18) moved the lock from 0.1.0 to 0.5.0. Matteo, 2026-10-22: "Fix examples in R8"; the docs pages that show it go to W5. The 2 Academy examples still fail on a separate import, `from flowcademy.academy import AcademyIntegrationAcademyIntegration` (there since `4d7b12e1`; the class is `flowgentic.academy.AcademyIntegration`). That is a follow-up, outside D10.
- [x] CI: the locked test matrix becomes the 1.x job.
- [x] Update `w4-release.md` "Tested environment" to the new locked versions.
- [x] Rerun R5 on the new versions: the substitution tests, the opt-in telemetry test, and the report's Versions line and numbers. Row 6 gets the dependency counts; the LangGraph 1.x section records the port instead of estimating it.

    Done-check: `uv run --extra dev pytest -q` is green, and CI on PR B is green with the new lock. R2's package check passes with the new pins. The 7 LangGraph example modules import (the 4 with `create_agent` calls among them), and the `create_agent` call shape (`model`, `tools`) runs against a scripted chat model. The report's Versions line names AsyncFlow 0.6.0 and the locked LangGraph.

## R5b LLM tool selection and handoff

Added by D10. Amendment A3 listed both paths as not tested, because the demo calls its tools from node code.

- [x] In `tests/integration/test_substitution.py`, add an offline test with a scripted chat model (no credentials). The model's tool calls run through LangGraph's `ToolNode`, in both versions with identical arguments:
    - Tool selection: the model calls a bounded science tool that runs as an AsyncFlow task. In the Flowgentic version that is an `AGENT_TOOL_AS_FUNCTION` tool; in the direct version, an AsyncFlow task wrapped with `langchain_core.tools.tool`.
    - Handoff: a supervisor agent hands off to a worker through Flowgentic's `create_task_description_handoff_tool`, and in the direct version through an equivalent tool that returns `Command(goto=Send(...), graph=Command.PARENT)`.
- [x] The handoff tool calls `GraphIntrospector.record_supervisor_event`, which commit `09b66146` (2025-10-16) deleted. Record the as-is failure as D1 evidence, then fix it by dropping the call, and test the handoff in both versions. Matteo, 2026-10-22: "Record, then fix".
- [x] Add the results to `docs/evaluation/substitution.md` as verdict rows, and take tool selection and handoffs out of "Not tested".

    Done-check: the test passes, asserting each version's observed behaviour, and the report's rows cite it.

