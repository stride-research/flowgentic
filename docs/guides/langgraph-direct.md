# LangGraph on AsyncFlow directly

A LangGraph application can run its tools, services and nodes as AsyncFlow
tasks without Flowgentic. This recipe is the port that the
[substitution experiment](../evaluation/substitution.md) made of the agentic
demo: `examples/ai-hpc-coupling/direct_agentic_application.py` keeps the
LangGraph graph, node bodies, tools, state and policy guards of
`agentic_application.py`, and replaces each Flowgentic decorator with a direct
AsyncFlow call. The planner's node also writes by hand the acceptance policy
that the Flowgentic version gets from `accept`; see
[Acceptance](../features/acceptance.md). From Flowgentic it imports only
`Rejected`, which the demo's shared checks and decision model raise.

`tests/integration/test_substitution.py` runs both versions with the same
arguments and finds the same decisions and results, in CI.

## 1. Create and shut down the engine

The application creates the `WorkflowEngine` on its backends and registers its
shutdown. The demo's runner uses an `AsyncExitStack`:

```python
--8<-- "examples/ai-hpc-coupling/agentic_campaign.py:open-direct"
```

`backends` is the same list of named local backends as in the Flowgentic
version (`compute` and `ai`).

## 2. Services and tasks

`flow.function_task` registers a task on a named backend; `service=True` makes
it a resident service. Calling a service task returns its future, and awaiting
the future gives the service:

```python
--8<-- "examples/ai-hpc-coupling/direct_agentic_application.py:direct-service"
```

The demo's reasoning steps are plain `@flow.function_task(backend="ai")`
tasks.

## 3. Tools and nodes

Two small helpers do what Flowgentic's `AGENT_TOOL_AS_FUNCTION` and
`EXECUTION_BLOCK` decorators did. `asyncflow_tool` wraps an AsyncFlow task in a
LangChain tool, with an optional retry; `asyncflow_block` runs a graph node as
an AsyncFlow block:

```python
--8<-- "examples/ai-hpc-coupling/direct_agentic_application.py:direct-helpers"
```

The tools stack `asyncflow_tool` on top of `flow.function_task`:

```python
--8<-- "examples/ai-hpc-coupling/direct_agentic_application.py:direct-tools"
```

and the graph's nodes are AsyncFlow blocks:

```python
--8<-- "examples/ai-hpc-coupling/direct_agentic_application.py:direct-blocks"
```

The same helper serves a model that selects a tool: the substitution tests
give `asyncflow_tool(description)(flow.function_task(run_simulation,
backend="compute"))` to LangGraph's `ToolNode`, and it behaves as the
Flowgentic tool does (`test_llm_selected_tool_runs_as_an_asyncflow_task`).

## 4. Retries

AsyncFlow 0.6.0 has no per-task retry or timeout, so the application writes
the policy it needs. The demo retries a simulation once after a transient
failure, with 5 s per attempt, and passes the policy to `asyncflow_tool` as
`retry=retry_transient_simulation`:

```python
--8<-- "examples/ai-hpc-coupling/campaign_common.py:retry-transient"
```

Nothing else is retried: graph nodes run once, with no timeout. The
Flowgentic version uses the same helper; see
[Retries and timeouts](retries-and-timeouts.md).

## 5. Handoffs

A handoff tool runs an AsyncFlow task that returns a LangGraph `Command`. The
direct branch of this test helper is the whole recipe, 17 lines against
Flowgentic's one call:

```python
--8<-- "tests/integration/test_substitution.py:handoff-tool"
```

## Run it

```bash
uv run --frozen --extra examples examples/ai-hpc-coupling/run_agentic_demo.sh application --integration direct
```

The run prints the same five cycles and proof points as the Flowgentic
version (see [Getting started](../getting-started.md)), and its
`agentic_demo_results/campaign_summary.json` records `"integration": "direct"`.
In this repository the direct run still loads Flowgentic, through the shared
runner and demo support code; a standalone direct application needs only
`radical-asyncflow`, `langgraph` and `langchain-core`.

## What changes without Flowgentic

| | With Flowgentic | Directly |
| --- | --- | --- |
| Engine | Created and shut down by `LangraphIntegration` | Created and shut down by the application |
| Tools, tasks, services, nodes | `asyncflow` decorator | `flow.function_task`, `flow.block`, `langchain_core.tools.tool` |
| Retry and timeout | Only what the application writes | Only what the application writes |
| Handoff tool | `create_task_description_handoff_tool` | About 17 lines |
| Accepting the planner's proposals | `accept`, with spans and evaluation events | The same policy in a hand-written loop, 3 more code lines, with no record in the trace |
| Third-party packages (fresh resolve, Python 3.10) | 74 | 42 |
