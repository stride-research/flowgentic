# Substitution experiment: the agentic demo with and without Flowgentic

This report is the evidence for decision D1: keep the LangGraph execution
adapter (`LangraphIntegration` and its `asyncflow` decorators) for v0.4.0, or
freeze it. It runs one agentic application twice, once through Flowgentic and
once on AsyncFlow directly, and records what the adapter demonstrably
supplies. A capability counts for the adapter only when the adapter supplies
it and the direct version does not.

## Setup

| | Flowgentic version | Direct version |
| --- | --- | --- |
| Application | `examples/ai-hpc-coupling/agentic_application.py` | `examples/ai-hpc-coupling/direct_agentic_application.py` |
| Integration | `LangraphIntegration` context; `asyncflow` decorators (`SERVICE_TASK`, `AGENT_TOOL_AS_FUNCTION`, `FUNCTION_TASK`, `EXECUTION_BLOCK`); introspected nodes | `WorkflowEngine.create` and `shutdown`; `flow.function_task` (`service=True` for the surrogate); `flow.block`; `langchain_core.tools.tool` |
| Runner | `agentic_campaign.py --integration flowgentic` | `agentic_campaign.py --integration direct` |

The LangGraph graph, node bodies, tools, state, policy guards and control loop
are the same in both versions; `diff` between the two application modules
shows only the integration layer. Both use the same decision model, budget,
backends (local `ai` and `compute`), simulation retry policy, service lifetime
and stopping rules, all from `open_campaign` in `agentic_campaign.py`.

Versions: Flowgentic 0.3.0 (`release/v0.3.0`), radical-asyncflow 0.5.0,
LangGraph 0.6.11, langchain-core 0.3.79, Python 3.10. Decisions come from the
offline rehearsal model (`--agent-mode rehearsal`), with failure injection on:
the first simulation of cycle 1 fails once and is retried.

Evidence is the test module `tests/integration/test_substitution.py`, run in CI
with `uv run --extra dev pytest -q tests/integration/test_substitution.py`.
Its telemetry test is opt-in (see row 7).

Retry policies (amendment A1). AsyncFlow 0.5.0 has no per-task retry or
timeout. The direct version reproduces only the demo's explicit simulation
policy, `SIMULATION_RETRY` in `demo_support.py` (2 attempts, 0.05 s back-off,
5 s per attempt), by reusing `retry_transient_simulation` from
`campaign_common.py`, which the RADICAL baseline already uses. Flowgentic also
wraps every other call, graph nodes included, in its default `RetryConfig`
(3 attempts, 30 s per attempt, retrying `TimeoutError`, `ConnectionError`,
`OSError` and some httpx and aiohttp errors). Row 3 reports what those
implicit defaults do.

## Results

| # | Criterion | Flowgentic version | Direct version | Verdict | Evidence |
| --- | --- | --- | --- | --- | --- |
| 1 | Results and scientific state transitions | 5 cycles, 20 simulations, 1 retry, stop on the uncertainty goal (0.2013), best x 2.76961 | Identical | Both supply it | `test_flowgentic_and_direct_versions_match`: the two `campaign_summary.json` files are equal once timing fields are dropped. That covers results, the 45-event sequence, the 15 agent decisions with their policy statuses, and the service counters. |
| 2 | Task and invocation identity | AsyncFlow assigns each task and block a uid and records its function name and target backend. The adapter adds node records keyed by node name and timestamp, not linked to AsyncFlow uids. | AsyncFlow uids, names and target backends | Both supply it | `test_both_versions_submit_the_same_asyncflow_components`: the same 14 components per cycle in both (4 blocks; 10 tasks: `load_surrogate`, `query_surrogate`, `reason_about_plan`, 4 × `run_simulation`, `update_surrogate`, `reason_about_evidence`, `coordinate_agents`). |
| 3 | Failure, retry and cancellation | Explicit simulation retry via `RetryConfig`. Implicit 3 × 30 s retry and timeout on every call and graph node. | Explicit simulation retry via `retry_transient_simulation`; no implicit retry | Both supply it | The explicit retry works in both: 1 retry each in `test_flowgentic_and_direct_versions_match`. Cancelling a cycle cancels its in-flight simulations in both: `test_cancelling_a_cycle_cancels_inflight_simulations`. The implicit defaults are the adapter's only addition, and they re-run scientific work. With a simulation that keeps failing, Flowgentic makes 24 attempts where the direct version makes 8, because the node retry re-runs the whole batch (`test_persistent_simulation_failure`). A node that outlasts the per-attempt timeout is cancelled and re-run with its simulations: 12 simulation starts against 4 (`test_node_exceeding_the_default_timeout_is_rerun`, with the 30 s default scaled down to 0.3 s). Per-task retry and timeout belong in AsyncFlow, which has neither in 0.5.0. |
| 4 | Service reuse and cleanup | AsyncFlow `service=True` task; the integration context shuts the engine down | AsyncFlow `service=True` task; `try`/`finally` shuts the engine down | Both supply it | `test_flowgentic_and_direct_versions_match`: one service load and identical request and update counts (5 and 5) in both, and no "never retrieved" or "destroyed but it is pending" warning on either run's stderr. Cancelling the resident service is application code in both (`open_campaign`). |
| 5 | Resource and token accounting | Tasks run on the named backends; observed compute parallelism 4. The report sums tokens from message metadata. | Same placement and parallelism; no token accounting | Resources: both supply it. Tokens: not tested. | `test_both_versions_place_tasks_on_the_named_backends`: each backend's own task registry shows 4 × `run_simulation` on `compute` and the six AI tasks on `ai`, in both versions. Parallelism comes from `test_flowgentic_and_direct_versions_match`. Token accounting needs a live LLM run, and none was possible without credentials; rehearsal decisions carry no token usage. |
| 6 | Application code and maintenance | Application module: 383 lines (335 code lines). Uses adapter code totalling 601 lines (`main.py`, `execution_wrappers.py`, `fault_tolerance.py`) plus 1,004 lines of telemetry. | Application module: 378 lines (323 code lines), including 32 lines of local helpers (`asyncflow_tool`, `asyncflow_block`) | Both supply it | Line counts are from `wc -l`, plus a count of non-blank, non-comment, non-docstring lines. The runner gained 84 lines and lost 20 to offer both versions. The direct version needs no more application code than the Flowgentic version, and imports nothing from Flowgentic. In this repository it still loads Flowgentic indirectly: the shared `agentic_support.py` imports `demo_support.py`, which imports Flowgentic for helpers the direct version does not use. Dependency difference: a fresh resolve of Flowgentic is 76 packages; the direct version's requirements (`radical-asyncflow==0.5.0`, `langgraph>=0.6.6,<1.0`, `langchain-core<1.0`) are 41. The 35 extra packages are Flowgentic, `langchain`, `langchain-openai`, `langchain-ollama`, `academy-py`, `python-json-logger` and their dependencies. Live mode needs a provider package (for example `langchain-openai`) in either version. See also LangGraph 1.x below. |
| 7 | Observability and reporting | `agent_execution_results/execution_summary.md`: per-node duration, state keys, messages and model names. It reports 0 tool calls and 0 tool executions here, because the demo calls its tools from node code. | No node-level report | Adapter supplies it: the node-level report. Task-level spans: both supply them (opt-in). | `test_flowgentic_and_direct_versions_match`: the report exists only for the Flowgentic run. AsyncFlow 0.5.0 emits OpenTelemetry spans once `flow.start_telemetry()` runs with RHAPSODY's `TelemetryManager` installed (`rhapsody-py[telemetry]`, a dependency of neither AsyncFlow nor Flowgentic). `test_asyncflow_telemetry_spans`, skipped unless RHAPSODY is installed, was run locally with `rhapsody-py[telemetry]==0.6.0`: 14 spans per cycle in each version (1 workflow, 4 blocks, 9 tasks), each task span carrying its uid, function name, status and duration. The spans label every task with the default backend (`compute`), including the AI tasks, which row 5 shows ran on `ai`. That labelling is an AsyncFlow defect. |

## LangGraph 1.x (amendment A4)

Row 6 counts the LangGraph 1.x port as a maintenance cost. To estimate it
without doing the port, Flowgentic was installed with `--no-deps` in a scratch
environment on LangGraph 1.2.16, LangChain 1.4.7, langchain-core 1.6.13,
langchain-openai 1.7.2 and langchain-ollama 1.1.0, with the same AsyncFlow and
Academy releases.

- Every Flowgentic module imports, and the full test suite passes unchanged:
  34 passed, 4 skipped, including the agentic demo and this experiment.
- The only API Flowgentic touches that LangGraph 1.0 deprecates is
  `langgraph.prebuilt.create_react_agent`, replaced by
  `langchain.agents.create_agent`, per the LangGraph v1 migration guide.
  `src/flowgentic` imports it in `langGraph/execution_wrappers.py` and
  `langGraph/utils/supervisor.py` but never calls it. Four example files call
  it 9 times.
- `Command`, `Send`, `StateGraph`, checkpointers, `ToolNode` and
  `InjectedState` are still present in 1.x.

Estimated port: lift the `langgraph<1.0` and `langchain-core<1.0` caps and
relock, delete the two unused imports, move the 9 example calls to
`create_agent` (`prompt` becomes `system_prompt`), and add a 1.x CI job. The
untested adapter paths (below) would need tests before the caps are lifted.

## Not tested (amendment A3)

The demo calls its four tools from node code with `.ainvoke`, so these adapter
features were not exercised and are unevaluated, not failed:

- LLM tool selection (`AGENT_TOOL_AS_FUNCTION` tools chosen by a model).
- Memory (`LangraphMemoryManager` is instantiated by the integration but
  unused).
- Agent handoffs (`create_task_description_handoff_tool`, `Command`, `Send`).
- The mutable graph (`langGraph/mutable_graph.py`).
- React agents (`create_react_agent`).
- Live LLM decisions, and with them token accounting (row 5).
- ADR control (`--controller adr`): unsupported in 0.3.0 (D6). It ran locally
  in both versions against an ADR checkout, but no test covers it.
- Remote or HPC backends: both versions ran on local backends only.

## Summary for D1

In this application the adapter supplies two things the direct version does
not:

- The node-level Markdown report. It misses tool executions that node code
  makes.
- Implicit retries and timeouts on every call and graph node. They re-run
  scientific work after a persistent failure or a slow node.

Everything else was supplied equally by both versions, with no more application
code in the direct version: results, task identity, the explicit retry,
cancellation, service reuse, cleanup and backend placement. Either way,
per-task retry and timeout are an unmet requirement for AsyncFlow 0.5.0. Moving
the adapter to LangGraph 1.x looks small for the paths the tests cover.
