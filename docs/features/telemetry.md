# Telemetry

Flowgentic records what each graph node did and writes it as a Markdown
report. The recorder is `GraphIntrospector` (`flowgentic.utils.telemetry`). It
works with or without `LangraphIntegration`, which creates one as
`integration.agent_introspector`.

For task-level traces, AsyncFlow itself emits OpenTelemetry spans; see
[AsyncFlow spans](#asyncflow-spans).

## Record the nodes

`introspector.introspect_node(node, name)` wraps a node. Each time the node
runs, the wrapper records its start and end time, the state before and after,
the messages it added, the tool calls and tool results in those messages, the
token usage and model name from the messages' metadata, and the keys that
changed to a non-empty value. Tool calls are read from the OpenAI-format
`tool_calls` in a message's `additional_kwargs`, so a model whose messages
carry them only in `AIMessage.tool_calls` shows none. The agentic demo wraps every node as it adds it to the graph,
and tells the introspector which nodes the report lists:

```python
--8<-- "examples/ai-hpc-coupling/demo_support.py:instrumented-nodes"
```

## Write the report

After the run, set the final state and call `generate_report(directory)`. It
writes `<directory>/agent_execution_results/execution_summary.md`, and the
`agent_execution_results` directory must exist. The demo's runner:

```python
--8<-- "examples/ai-hpc-coupling/agentic_campaign.py:write-report"
```

The report paths come from the `agent_execution` section of `config.yml`.
Flowgentic reads it at import from the first place that has one: the working
directory, `~/.flowgentic/config.yml`, the file named by `FLOWGENTIC_CONFIG`,
or the root of a source checkout. Without one, it uses the same paths as
defaults.

`await integration.generate_execution_artifacts(app, __file__, final_state=state)`
does the same for a script, once `agent_introspector._all_nodes` is set (without
it, writing the report raises `ValueError`): it creates `agent_execution_results/` next to the
calling file, writes the report, and renders the graph to
`agent_graph.png` at the top of the report. Rendering needs `pygraphviz`, which
is not a Flowgentic dependency; without it, the call prints an error and keeps
the report. Passing `generate_report=True` skips the report.

## Read the report

The report from `make examples-ai-hpc-agentic` starts with totals and one row
per node execution:

```text
# LangGraph Execution Report

**Generated on:** `2026-10-22 14:34:44`
**Total Duration:** `2.0956 seconds`

## Aggregate Statistics

- **Total Tokens Used:** `0`
- **Total Tool Calls:** `0`
- **Total Tool Executions:** `0`
- **Total Messages:** `1`
- **Models Used:** `rehearsal-agents, deterministic-surrogate`
- **Number of Nodes:** `20`
```

Then come the Execution Summary table (duration, tokens, tools and new
messages per node), the Node Details (description, timing, message counts,
state keys, model and token usage, tool calls and their results, the messages
added and the state changes) and a Final State Summary. A Memory Statistics
section appears when the final state has a `memory_stats`,
`memory_operations` or `memory_context` field.

The report counts only what the messages carry. The demo's nodes call their
tools from code, not through model tool calls, so it shows 0 tool calls; and
its rehearsal model records no tokens.

## Without the adapter

`GraphIntrospector` does not need `LangraphIntegration`. The substitution
tests wrap the direct version's nodes and write the same report; see
[LangGraph on AsyncFlow directly](../guides/langgraph-direct.md#6-the-node-report-without-the-adapter).

## AsyncFlow spans

AsyncFlow 0.6.0 emits OpenTelemetry spans for workflows, blocks and tasks once
`flow.start_telemetry()` runs with RHAPSODY's telemetry installed
(`rhapsody-py[telemetry]`), which neither AsyncFlow nor Flowgentic depends on.
The opt-in test `test_asyncflow_telemetry_spans` in
`tests/integration/test_substitution.py` checks them: per cycle, 1 workflow
span, 4 block spans and 9 task spans, each task span with `task_id`,
`executable` and `status`. In AsyncFlow 0.5.0 and 0.6.0, every task span names
the default backend, even for tasks that ran on another one.

## API

[`flowgentic.utils.telemetry.introspection`](../api/flowgentic/utils/telemetry/introspection.md)
