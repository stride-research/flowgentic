# Changelog

All notable changes to Flowgentic are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Tags `v0.1.0` (`3e43c13c`, 23 Sep 2025) and `0.2.0` (`72e972d4`, 2 Oct 2025)
predate this changelog, so their changes are not listed here.

## [Unreleased]

### Changed

- **Breaking:** Academy is an optional dependency, the `academy` extra, at
  `academy-py>=1.0,<2`; the lock moves from academy-py 0.3.0 to 1.0.0. A plain
  install no longer brings Academy and its redis, globus-sdk and aiohttp.
  Importing `flowgentic.academy` without the extra raises an `ImportError`
  that names it: install `flowgentic[academy]`.
- `AcademyIntegration` runs agents on the manager's event loop, as Academy's
  manager does by default, instead of a thread pool. On the pool, each agent
  held a worker for its whole life, and agents beyond the pool's size, at most
  32, never started.
- `AcademyIntegration` launches wait for the agent to answer a ping instead of
  a fixed sleep: `startup_delay` defaults to 0, and the new `startup_timeout`
  (60 s) bounds the wait, after which the agent is shut down.

### Fixed

- `flowgentic.academy`: an action called from an AsyncFlow task, whether a
  function task, a block or a chain from `create_workflow_from_academy_chain`,
  failed with `ExchangeClientNotFoundError`. Launches and actions now run on
  the manager's event loop, through handles bound to its exchange client.
- `flowgentic.academy`: the action's result was awaited a second time, so a
  call failed even when the action succeeded.
- `flowgentic.academy`: concurrent first calls for one `agent_id` could each
  launch an agent.
- `flowgentic.academy`: entering `AcademyIntegration` reconfigured the
  application's logging through Academy's `init_logging`, which academy-py 1.0
  removed.
- The Academy examples imported a module that does not exist. They run, and
  CI runs them.
- The retry loop re-raised `ModuleNotFoundError` when `httpx` or `aiohttp` was
  missing from the environment; it now leaves their exceptions out of the
  default retryable ones. Without the fix, every decorated call on the default
  retry config would fail in an install without the `academy` extra, the only
  source of `aiohttp`.

## [0.3.1] - 2026-10-23

A patch release: fixes for the defects that 0.3.0's documentation lists.

### Fixed

- Memory, `trim_middle`: with one free place beside the system messages, every
  message was kept. The strategy now fills every free place, and the most recent
  messages take the odd place of an odd count.
- Memory, `importance_based`: after the first trim, each message was scored by
  another message's importance. The scores now follow the trimmed history, which
  also corrects the importance weighting in `get_relevant_context`.
- Memory, `summarize`: the summary could take the history one message over the
  limit. It now fits within the limit. With one free place, or when no summary
  is made, the strategy trims as `trim_last` does; a failed summary used to
  leave its place empty, or keep the oldest messages.
- `MutableGraph.update_graph` returns `False` for a replacement node that is
  already active. It used to list the node twice, and the rebuild raised
  LangGraph's `ValueError`.
- `generate_execution_artifacts(generate_report=...)` was inverted: `True`
  skipped the report. `True` now writes it and `False` skips it, and the default
  becomes `True`, so a call without the flag still writes the report.
- Telemetry: tool calls are read from `AIMessage.tool_calls`, and malformed ones
  from `invalid_tool_calls`. Only OpenAI-format `additional_kwargs` were read,
  so other models' tool calls were missing from the report.
- Telemetry: an AI message's text is recorded as the node's final response even
  when the message has no `additional_kwargs`, and list content is joined into
  text.

## [0.3.0] - 2026-10-22

A consolidation release. It preserves the work since 0.2.0 as a supported,
documented snapshot: fixes and evidence, no new features beyond those already
on `main`.

### Added

- `flowgentic.agent.bind_agent`: binds a framework-specific agent component to
  an AsyncFlow task on a named backend; each call runs the component as that
  task and returns its structured decision with the agent's identity and
  timing.
- Named AsyncFlow backends: Flowgentic's `asyncflow` decorator takes `backend=`
  and passes it to AsyncFlow, except for `EXECUTION_BLOCK`.
- Service tasks (`AsyncFlowType.SERVICE_TASK`) for resident AsyncFlow
  services, and `MutableGraph` (`flowgentic.langGraph.mutable_graph`) for
  graphs changed at runtime.
- AI-HPC coupling demos in `examples/ai-hpc-coupling/`: a RADICAL-only
  baseline, a deterministic LangGraph version, an agentic version and an
  agent-augmented version, with offline rehearsal decisions and optional ADR
  control.
- `--integration direct` for the agentic demo (`direct_agentic_application.py`)
  runs the same agent graph on AsyncFlow without Flowgentic, and the run's
  `campaign_summary.json` records which integration ran. The substitution
  experiment compares the two (`tests/integration/test_substitution.py`,
  `docs/evaluation/substitution.md`), including LLM tool selection and an agent
  handoff driven by a scripted chat model.
- An `examples` extra (`python-dotenv`), included in `dev`.
- CI: a strict MkDocs build job, and Python 3.12 in the test matrix.
- Tests that run the offline mutable-graph and service-task examples
  (`tests/integration/test_feature_examples.py`).
- This changelog.

### Changed

- Breaking: the decorator API moved from
  `integration.agents.asyncflow(flow_type=TOOL | NODE | UTISL_TASK | BLOCK)` to
  `integration.execution_wrappers.asyncflow(flow_type=AGENT_TOOL_AS_FUNCTION |
  FUNCTION_TASK | SERVICE_TASK | EXECUTION_BLOCK)`. Execution reports moved from
  `integration.agent_logger` to `integration.agent_introspector`
  (`flowgentic.utils.telemetry`).
- Python 3.10 or later is required (0.2.0 allowed 3.9). CI tests 3.10, 3.11
  and 3.12.
- Dependencies are released versions:
  - `radical-asyncflow==0.6.0` and `academy-py==0.3.0` come from PyPI instead
    of git URLs.
  - LangGraph, langchain-core and langchain move to 1.x
    (`>=1.0,<2.0`); the lock tests LangGraph 1.2.16 and langchain-core 1.6.13.
  - `python-json-logger>=3.1.0`.
  - `pyyaml` is declared.
- `python-dotenv` moved from the core dependencies to the `examples` extra.
- LangGraph 1.x's `ToolNode` re-raises tool errors by default, where 0.6
  returned them to the model as messages. A Flowgentic tool that still fails
  after its retries now aborts the graph run; pass `handle_tool_errors=True` to
  `ToolNode` to keep the 0.6 behaviour.
- The LangGraph examples build their agents with `langchain.agents.create_agent`,
  which replaces LangGraph's deprecated `create_react_agent`.
- The AI-HPC demo launchers use the installed AsyncFlow, and import a local
  checkout only when `ASYNCFLOW_SRC` is set. `make
  examples-ai-hpc-deterministic` and `make examples-ai-hpc-agentic` create the
  environment and run in a fresh clone.
- ADR mode (`--controller adr`) is documented as unsupported. It needs an ADR
  source checkout (`ADR_SRC`).
- Development uses `uv` and the checked-in `uv.lock`. CI checks formatting with
  the locked Ruff, and builds the documentation strictly from the locked
  environment.
- The documentation site describes 0.3.0: its status, a getting-started page,
  guides for `bind_agent`, for `LangraphIntegration` and for LangGraph on
  AsyncFlow without Flowgentic, a project status page and this changelog. Its
  code blocks include tested files, and the strict build also fails on broken
  links and anchors. The README matches the site's home page.

### Removed

- The `langchain-community` and `langchain-mcp-adapters` dependencies, which
  nothing imported.
- The `flowgentic` console script, whose target `flowgentic:main` did not
  exist.
- The committed results folder `examples/ai-hpc-coupling/agentic_demo_results/`.
- Two unused `create_react_agent` imports from `flowgentic.langGraph`.
- Documentation pages for code that is not on `main`: MCP tools
  (`AGENT_TOOL_AS_MCP`, archived as `archive/feature/mcp`), the framework ×
  backend roadmap, the hierarchical design pattern, which has no example, and
  the chatbot pattern, whose interactive example no test runs.

### Fixed

- Installing the wheel with pip. It could resolve `python-json-logger` 2.x,
  which lacks the `pythonjsonlogger.json` module Flowgentic imports, and
  `pyyaml` was imported but not declared.
- The agentic demos' candidate policy no longer rejects a proposal at a bound
  for rounding alone, and never returns a candidate outside its bounds.
- `create_task_description_handoff_tool`: every handoff failed with
  `AttributeError`, because the tool called a `GraphIntrospector` method removed
  in October 2025. The call is gone, and handoffs work, with Pydantic and
  TypedDict graph states (such as `create_agent`'s).
- The LangGraph examples import again: they used `ConcurrentExecutionBackend`,
  which AsyncFlow 0.3.0 and later do not export, and now use
  `LocalExecutionBackend`.
- Seven `mkdocs build --strict` warnings: three missing annotations and four
  broken links.
- The mutable-graph example's conditional graph always took the standard
  route: its routing flag was missing from the state schema that the graph and
  the router read.
- The supervisor toy example stored agent B's result as `agent_b`, and its
  gather node, which reads `agent_B`, passed "N/A" to the synthesis model.
- CI:
  - `astral-sh/setup-uv` is pinned to an existing release (it publishes no
    `v9` tag).
  - Each test matrix job runs its own Python with the dev extra.
  - The bandit job runs again.

[0.3.1]: https://github.com/stride-research/flowgentic/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/stride-research/flowgentic/compare/0.2.0...v0.3.0
