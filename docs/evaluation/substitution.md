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

The LangGraph graph, node bodies, tools, state and policy guards are the same
in both versions. A `diff` of the two application modules shows only the
integration layer, plus the control loop: it stays in `agentic_application.py`,
and the runner uses it for both versions. Both use the same decision model,
budget, backends (local `ai` and `compute`), simulation retry policy, service
lifetime and stopping rules, all from `open_campaign` in `agentic_campaign.py`.

Versions: Flowgentic 0.3.0 (`release/v0.3.0`), radical-asyncflow 0.6.0,
LangGraph 1.2.16, langchain-core 1.6.13, langchain 1.4.7, Python 3.10. R5 first
ran on AsyncFlow 0.5.0 and LangGraph 0.6.11. R8 moved the lock to the versions
above, and every test and number in this report was rerun on them with the same
results. Decisions come from the
offline rehearsal model (`--agent-mode rehearsal`), with failure injection on:
the first simulation of cycle 1 fails once and is retried.

Evidence is the test module `tests/integration/test_substitution.py`, run in CI
with `uv run --extra dev pytest -q tests/integration/test_substitution.py`.
Its telemetry tests are opt-in (see row 7). Anything not backed by a test is
marked as code reading or as an observation from a demo run.

Retry policies (amendment A1). AsyncFlow has no per-task retry or timeout, in
0.6.0 as in 0.5.0. The direct version reproduces only the demo's explicit simulation
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
| 2 | Task and invocation identity | AsyncFlow assigns each task and block a uid and records its function name and target backend. The Flowgentic report adds node records keyed by node name and timestamp, not linked to AsyncFlow uids. | AsyncFlow uids, names and target backends | Both supply it | `test_both_versions_submit_the_same_asyncflow_components`: a one-cycle run registers the same 14 components in both versions, each under its own uid: 13 per cycle (4 node blocks and 9 tasks: `query_surrogate`, `reason_about_plan`, 4 × `run_simulation`, `update_surrogate`, `reason_about_evidence`, `coordinate_agents`) plus the one-off `load_surrogate` service. |
| 3 | Failure, retry and cancellation | Explicit simulation retry via `RetryConfig`. Implicit retry and timeout (3 attempts × 30 s) on every call and graph node. | Explicit simulation retry via `retry_transient_simulation`; no implicit retry or timeout | Explicit retry and cancellation: both supply it. Implicit call and node retry and timeout: the adapter supplies it, and here it is harmful. | The explicit retry works in both: 1 retry each in `test_flowgentic_and_direct_versions_match`. Cancelling a cycle cancels its in-flight simulations in both: `test_cancelling_a_cycle_cancels_inflight_simulations`. The implicit defaults re-run scientific work. With a simulation that keeps failing, Flowgentic makes 24 attempts where the direct version makes 8, because the node retry re-runs the whole batch (`test_persistent_simulation_failure`). In `test_node_exceeding_the_default_timeout_is_rerun`, the 30 s default is scaled down to 0.3 s: a node that outlasts it is cancelled and re-run with its simulations (12 starts against 4), and after three attempts the Flowgentic cycle fails with `TimeoutError`, while the direct version completes. With the real default, the same code path fails any graph node whose work takes longer than 30 s. Both versions write their own per-task retry because AsyncFlow (0.5.0 and 0.6.0) has none. |
| 4 | Service reuse and cleanup | AsyncFlow `service=True` task. The integration context, entered on the runner's `AsyncExitStack`, shuts the engine down. | AsyncFlow `service=True` task. An `AsyncExitStack` callback shuts the engine down. | Both supply it | `test_flowgentic_and_direct_versions_match`: one service load and identical request and update counts (5 and 5) in both, both runs exit 0, and neither stderr has a "never retrieved" or "destroyed but it is pending" warning. No test asserts the shutdown itself. Cancelling the resident service is application code in both (`open_campaign`). |
| 5 | Resource and token accounting | Tasks run on the named backends; observed compute parallelism 4. The node report sums tokens from message metadata. | Same placement and parallelism | Resources: both supply it. Tokens: not tested. | `test_both_versions_place_tasks_on_the_named_backends`: each backend's own task registry shows 4 × `run_simulation` on `compute` and the six AI tasks on `ai`, in both versions. Parallelism comes from `test_flowgentic_and_direct_versions_match`. Tokens need a live LLM run, and none was possible without credentials. Code reading says a live run would record 0 tokens in both versions anyway: `decision_message` in `agentic_support.py` sets usage to zero, and `LangChainDecisionModel._decide` calls `with_structured_output`, which returns the parsed decision rather than the message that carries usage. Recording tokens is a job for the application's decision-model layer; Flowgentic's report can only sum what the messages carry. |
| 6 | Application code and maintenance | Application module without the shared control loop: 362 lines (319 code lines). The adapter is 601 lines (`main.py`, `execution_wrappers.py`, `fault_tolerance.py`). `LangraphIntegration` also creates a memory manager (`memory.py`, 723 lines) that this demo never uses. | Application module: 378 lines (323 code lines), including 32 lines (22 code) of local helpers (`asyncflow_tool`, `asyncflow_block`) | Both supply it, in about the same code: the direct version has 4 more code lines. The adapter adds code and dependencies to maintain. | Line counts are from `wc -l`, plus a count of non-blank, non-comment, non-docstring lines. The Flowgentic module's control loop (`run_agentic_with_application_control`, 16 code lines) is left out because both versions use it. To offer both versions, the runner gained 76 lines and lost 20. `direct_agentic_application.py` imports nothing from Flowgentic. A direct run in this repository still loads Flowgentic through shared code: the runner imports `LangraphIntegration` and `agentic_application.py`, and `agentic_support.py` imports `demo_support.py`, which imports Flowgentic. A standalone direct application would need none of it. Dependencies (fresh resolve, Python 3.10): Flowgentic's come to 74 third-party packages, the direct version's (`radical-asyncflow==0.6.0`, `langgraph>=1.0,<2.0`, `langchain-core>=1.0,<2.0`) to 42. That is 32 more, plus Flowgentic itself, among them `langchain`, `langchain-openai`, `langchain-ollama`, `academy-py` and `python-json-logger`. Live mode needs a provider package (for example `langchain-openai`) in either version. See also the LangGraph 1.x port below. |
| 7 | Observability and reporting | `agent_execution_results/execution_summary.md`: per-node duration, state keys, messages and model names | No report unless the application wires in Flowgentic's telemetry module | Node-level report: Flowgentic's telemetry module supplies it, without needing the execution adapter. Task-level spans: both supply them (opt-in). | `test_flowgentic_and_direct_versions_match`: only the Flowgentic run writes the report. `test_flowgentic_report_works_without_the_adapter`: Flowgentic's `GraphIntrospector` (`flowgentic.utils.telemetry`), wrapped around the direct version's nodes, produces the node-level report with no `LangraphIntegration`. The application has to wrap the nodes and set the introspector's private `_all_nodes` and `_final_state`; the Flowgentic version does the same through `add_instrumented_nodes` and the runner. Observed in a demo run's report: it shows 0 tool calls and 0 tool executions, because node code calls the tools directly. AsyncFlow 0.6.0 emits OpenTelemetry spans once `flow.start_telemetry()` runs with RHAPSODY's `TelemetryManager` installed (`rhapsody-py[telemetry]`, a dependency of neither AsyncFlow nor Flowgentic). `test_asyncflow_telemetry_spans` is skipped unless RHAPSODY is installed; it was run locally with `rhapsody-py[telemetry]==0.6.0`. In both versions it asserts 1 workflow span, 4 block spans and 9 task spans per cycle, with each task span carrying `task_id`, `executable` and `status`. It also asserts that every task span is labelled with the default backend (`compute`), including the AI tasks, which row 5 shows ran on `ai`. That mislabelling is an AsyncFlow defect, present in 0.5.0 and 0.6.0. |
| 8 | LLM tool selection (R5b) | A scripted model's call to `run_simulation`, an `AGENT_TOOL_AS_FUNCTION` tool, runs through LangGraph's `ToolNode` as an AsyncFlow task on `compute` | The same, with the AsyncFlow task wrapped by `langchain_core.tools.tool` through the direct version's `asyncflow_tool` helper | Both supply it | `test_llm_selected_tool_runs_as_an_asyncflow_task`: in both versions the graph produces a human message, the model's tool call, a successful tool message with identical content, and the model's answer. AsyncFlow registers exactly one `run_simulation` task on `compute` per version. The model is scripted (`GenericFakeChatModel`), so this tests the tool path, not a live model's choice. |
| 9 | Agent handoff (R5b) | `create_task_description_handoff_tool` builds a tool that runs an AsyncFlow task returning `Command(goto=[Send(...)], graph=Command.PARENT)`. As shipped since `09b66146` (2025-10-16), it failed on every call: it called `GraphIntrospector.record_supervisor_event`, which that commit deleted. R5b removed the call. The code review of R5b then found that the tool also failed on a TypedDict state (`MessagesState`, or `create_agent`'s), because it called `model_dump()`; it now accepts both. | A 17-line tool that builds the same `Command` in an AsyncFlow task | Both supply it after R5b's fix; before the fix, only the direct version did. | `test_supervisor_hands_off_to_a_worker`, for both versions and for both a Pydantic and a TypedDict graph state: a supervisor subgraph's scripted tool call hands the task to the parent graph's `researcher` node, which receives exactly the task description. Before the fix, the Flowgentic case failed with `AttributeError: 'GraphIntrospector' object has no attribute 'record_supervisor_event'` (the failing run is recorded in PR B's body), and the direct case passed. |

## LangGraph 1.x and AsyncFlow 0.6.0 (amendment A4, R8)

Row 6 counts the LangGraph 1.x port as a maintenance cost. R5 first estimated
it in a scratch environment, where the full suite passed unchanged on
LangGraph 1.2.16. D10 then moved the port into PR B as R8. The port took:

- Pins: `langgraph>=1.0,<2.0`, `langchain-core>=1.0,<2.0`,
  `langchain>=1.0,<2.0` and `radical-asyncflow==0.6.0`, relocked for those
  packages only. The floors were raised to 1.0, so no CI job on a 0.6 lower
  bound is needed; the locked CI matrix is the 1.x job.
- Flowgentic source: deleting the two unused `create_react_agent` imports,
  in `langGraph/execution_wrappers.py` and `langGraph/utils/supervisor.py`.
  Nothing else changed. `Command`, `Send`, `StateGraph`, checkpointers,
  `ToolNode` and `InjectedState` are unchanged in 1.x.
- Examples: the 9 `create_react_agent` calls (4 files) moved to
  `langchain.agents.create_agent`, which the LangGraph v1 migration guide names
  as the replacement. They passed only `model` and `tools`, so no argument
  changed. The example files also imported `ConcurrentExecutionBackend`, which
  no AsyncFlow release on this history exports; R8 renamed it to
  `LocalExecutionBackend`, and the 7 LangGraph example modules import again.
- Result at R8: 40 passed, 4 skipped on the new lock, with no warnings; with
  R5b's tests and its code review, 48 passed, 4 skipped. The substitution tests and the opt-in
  telemetry tests give the same results as on LangGraph 0.6.11 and AsyncFlow
  0.5.0.
- Behaviour change: LangGraph 1.x's `ToolNode` re-raises tool errors by
  default, where 0.6 returned them to the model as messages. A Flowgentic tool
  that still fails after its retries now aborts the graph run, unless the
  application passes `handle_tool_errors=True`.

AsyncFlow 0.6.0 still has no per-task retry or timeout, and its spans still
label every task with the default backend. The docs snippets that still show
`create_react_agent` belong to W5's design-pattern audit.

## Not tested (amendment A3)

The agentic demo calls its three tools (`query_surrogate`, `run_simulation`,
`update_surrogate`) from node code with `.ainvoke`, and its three reasoning
functions as plain AsyncFlow tasks. R5b's scripted tests (rows 8 and 9) cover
LLM tool selection and handoffs outside the demo. These adapter features
remain unexercised; they are unevaluated, not failed:

- Memory: the integration creates a `LangraphMemoryManager`, which this demo
  never uses.
- The mutable graph (`langGraph/mutable_graph.py`).
- Live LLM decisions, and with them token accounting (row 5).
- ADR control (`--controller adr`): unsupported in 0.3.0 (D6). It ran locally
  in both versions against an ADR checkout, but no test covers it.
- Remote or HPC backends: both versions ran on local backends only.

## Summary for D1

In this application, the execution adapter's only distinct contribution is its
implicit retry and timeout on every call and graph node. The tests show it
re-running scientific work, and failing any node that outlasts the per-attempt
timeout (30 s by default).

The node-level report comes from Flowgentic's telemetry module, which also
works on the direct version without the adapter. Everything else is supplied
equally, in about the same application code (the direct version has 4 more
code lines) and with 32 fewer third-party packages: results, task identity,
the explicit retry, cancellation, service reuse, cleanup, backend placement,
LLM tool selection and handoffs.

The adapter's handoff tool had failed on every call since October 2025, when
a refactor deleted the introspection method it called. PR B removes the call;
no test had covered the path. Neither version records tokens, and by code reading the gap is in
the application's decision-model layer.

Design recommendation: per-task retry and timeout belong in AsyncFlow, which
has neither in 0.6.0, so today each application writes its own. The move to
LangGraph 1.x and AsyncFlow 0.6.0 (R8) cost two deleted imports in Flowgentic
and a rename in 9 example calls.
