# Changelog

All notable changes to Flowgentic are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Tags `v0.1.0` (`3e43c13c`, 23 Sep 2025) and `0.2.0` (`72e972d4`, 2 Oct 2025)
predate this changelog, so their changes are not listed here.

## [Unreleased]

### Changed

- **Breaking:** `import flowgentic` configures nothing. It used to set the root
  logger to DEBUG, add a handler that printed every record to stdout, start a
  thread and register an `atexit` hook; in test runs, that handler wrote to
  streams the runner had closed. The `flowgentic` logger now has no handler:
  without configuration, warnings and errors reach stderr through Python's
  last-resort handler, and the rest is the application's. To get Flowgentic's
  records back, call `flowgentic.configure_logging()`, which prints them in
  plain text to stderr; see
  [Logging](https://stride-research.github.io/flowgentic/features/logging/).
- Log records follow a level contract: DEBUG for per-call detail, INFO for
  lifecycle events, WARNING for degraded results and refused requests, ERROR
  for failures. Per-call records that were INFO are DEBUG, such as the
  decorator's "Registering function … as AsyncFlow …". `MutableGraph`'s
  refusals, which return `False`, are WARNING. No record carries prompts,
  model outputs, message lists or graph states, at any level. Records use lazy
  `%`-style arguments, and CI checks it.
- **Breaking:** `generate_execution_artifacts` takes `output_dir`, the
  directory for the report and the graph image, by default
  `agent_execution_results/` beside the calling file, as before.
  `GraphIntrospector.generate_report(directory)` and `render_graph` write into
  the directory they are given, no longer into its `agent_execution_results/`
  subdirectory.
- The documentation site follows the latest release: it deploys when the
  repository's latest GitHub release is published (not a pre-release, nor a
  patch to an older line), or by hand for a given release tag, no longer on
  every push to `main`. It stops describing changes that no release contains.
- CI runs four design-pattern examples, `sequential/research_agent`,
  `sequential/research_agent_memory`, `supervisor/toy` and
  `supervisor/product_research`, with a scripted chat model in place of
  `ChatLLMProvider`, and checks that each completes and writes its report.
  They need OpenRouter credentials, so CI had never run them. Live runs stay
  opt-in: `make tests-integration-llm` locally, or the LLM examples workflow,
  which a maintainer starts by hand on GitHub with the repository secret
  `OPEN_ROUTER_API_KEY`.
- Dependencies are ranges, not pins: `radical-asyncflow>=0.6,<0.7`, where 0.3
  required `==0.6.0`, because before 1.0 an AsyncFlow minor release may break;
  `langchain-openai` and `langchain-ollama` at `>=1.0,<2.0`, like the other
  LangChain packages. The `examples` and `dev` extras have floors, and `dev`
  keeps `pymdown-extensions` below 12.2: 12.2 breaks mkdocstrings before 1.0.7,
  and 1.0.7 needs Python 3.11. CI runs the suite on the lowest versions the
  ranges allow. A weekly workflow runs the suite and the docs build on the
  newest releases within them, and the suite on the newest AsyncFlow and Academy
  releases beyond them. Dependabot updates `uv.lock` and the workflows' actions.

### Removed

Nobody uses Flowgentic yet, so these go without a deprecation period.

- **Breaking:** the retry loop around every decorated call, with
  `flowgentic.langGraph.fault_tolerance` (`RetryConfig`,
  `LangraphToolFaultTolerance`) and the `asyncflow` decorator's `retry=`
  argument. Every tool, task, service and graph node ran in it, by default
  with 3 attempts of at most 30 s each, retrying `OSError` among other errors.
  On graph nodes it re-ran finished scientific work: with a simulation that
  kept failing, it made 24 attempts where the application's own policy made 8,
  and it cancelled and re-ran a node whose work took longer than 30 s until
  the cycle failed
  ([substitution experiment](https://stride-research.github.io/flowgentic/evaluation/substitution/),
  row 3). Each call now runs once. A model call's retries and deadline belong
  to the chat model (`max_retries`, `timeout`), a graph node's to LangGraph
  (`add_node(..., retry_policy=..., timeout=...)`), and a task's to the
  application until AsyncFlow has per-task retry, as in the AI-HPC demos. See
  [Retries and timeouts](https://stride-research.github.io/flowgentic/guides/retries-and-timeouts/).
  The decorator's keyword arguments are now explicit, so an unknown one, such
  as `retry=`, raises `TypeError`; `tool_description` was the only other.
- **Breaking:** `flowgentic.academy` (`AcademyIntegration`) and its two
  examples. In 0.3 it could not run an Academy action through AsyncFlow: every
  call failed with `ExchangeClientNotFoundError`, and the examples failed at
  import. Repaired and then compared with direct Academy calls in AsyncFlow
  tasks, it added no capability: the same results, tasks and agent lifecycle,
  for 6 fewer lines of application code
  ([Academy substitution check](https://stride-research.github.io/flowgentic/evaluation/academy/)).
  The guide [Academy actions as AsyncFlow tasks](https://stride-research.github.io/flowgentic/guides/academy-on-asyncflow/)
  now shows the direct calls, with a tested example. Placing Academy agents
  on HPC resources belongs to RADICAL's execution layer, and an agent that
  drives the workflow to ADR. Flowgentic no longer depends on academy-py; the
  `examples` extra brings `academy-py>=1.0,<2` for the example.
- The settings file: `config.yml`, looked up at import in the working
  directory, `~/.flowgentic/`, the file named by `FLOWGENTIC_CONFIG` and the
  source tree, and the `flowgentic.settings` module. An unrelated `config.yml`
  in the working directory made `import flowgentic` fail with `KeyError`, and
  the file held only the log level, now the application's, and the report's
  paths, now `output_dir`.
- `flowgentic.utils.Logger`, its colored and JSON formatters, the base64
  redaction filter and `flowgentic.utils.add_context_to_log`. Use
  `flowgentic.configure_logging()` or the application's own logging
  configuration; OpenTelemetry's logging instrumentation adds trace and span
  ids to every record.
- The `pyyaml` and `python-json-logger` dependencies.
- `render_graph`'s `generate_graph_only` argument. With it, the call did
  nothing. To draw only the graph, call LangGraph's
  `app.get_graph().draw_png(path)`.

### Fixed

- `langgraph` 1.1.5 or later is required; 0.3 allowed 1.0. langgraph 1.0 to
  1.1.4 install with the newest `langgraph-prebuilt` 1.0.x, which imports
  names that langgraph 1.1.5 added, so importing Flowgentic's LangGraph
  integration failed with `ImportError`.
- `generate_execution_artifacts(..., generate_report=False)`, or any call whose
  output directory had no `execution_summary.md`, wrote no graph section and
  left an empty `execution_summary.md`: `render_graph` failed on the missing
  report and logged only a warning (in 0.3.0, a print). It now starts the
  report with the graph section. With `generate_report=False`, an earlier
  run's report in the same directory is removed rather than kept under a new
  graph section. Only drawing the graph, which needs `pygraphviz`, ends in a
  warning; an error after it, such as an unreadable report, raises. The
  report is read and rewritten as UTF-8, as it is written, rather than in the
  locale's encoding.
- The `supervisor/toy` example hid the error of a failed run: when the graph
  raised, the `finally` block that writes the report read a result that was
  never set, and the run ended in `UnboundLocalError`. The graph's own error
  now propagates, after the report is written.
- The `sequential/research_agent` example exited 0 when a stage failed: its
  nodes route failures to the error handler, so a live run in which every
  model call failed, for want of a key or in an outage, passed
  `tests/integration/test_integration.py`, which checks only the exit code.
  A run that does not complete now raises `RuntimeError` with the stages'
  errors.

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
