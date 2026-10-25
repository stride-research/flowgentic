# LangGraph on AsyncFlow with Flowgentic

`LangraphIntegration` runs a LangGraph graph's tools, services, tasks and nodes
as AsyncFlow tasks. It opens an AsyncFlow `WorkflowEngine`, and its
`asyncflow` decorator turns an async function into a LangChain tool, an
AsyncFlow task, a resident service or a graph node.

!!! warning "Frozen in maintenance mode"
    The execution adapter gets no new features. The
    [substitution experiment](../evaluation/substitution.md) ran the same
    application with and without it. Without it, the application kept the same
    results, task identity, cancellation, service reuse and backend placement,
    in about the same code. Up to 0.3, the adapter also wrapped every call and
    graph node in an implicit retry and timeout, which re-ran scientific work;
    0.4.0 removes them. For new applications, see
    [LangGraph on AsyncFlow directly](langgraph-direct.md).

The code on this page comes from the agentic demo,
`examples/ai-hpc-coupling/agentic_application.py`, and from
`tests/integration/test_substitution.py`. Both run in CI: the demo through
`tests/integration/test_ai_hpc_agentic_demo.py` and the substitution tests.

## Open the integration

`LangraphIntegration(backend=...)` takes one AsyncFlow backend or a list of
named backends. The demo creates two local backends, `compute` and `ai`:

```python
--8<-- "examples/ai-hpc-coupling/campaign_common.py:local-backends"
```

Use the integration as an async context manager. Entering it creates the
`WorkflowEngine`; leaving it shuts the engine down. The demo's runner enters it
on an `AsyncExitStack`:

```python
--8<-- "examples/ai-hpc-coupling/agentic_campaign.py:open-flowgentic"
```

Inside the context, the integration exposes:

| Attribute | Content |
| --- | --- |
| `flow` | The AsyncFlow `WorkflowEngine` |
| `execution_wrappers` | The `asyncflow` decorator and the handoff tool |
| `agent_introspector` | The node report; see [Telemetry](../features/telemetry.md) |
| `memory_manager` | Conversation memory; see [Memory](../features/memory.md) |

The integration always creates its own engine: it cannot run on an engine the
application has already created.

## Decorate functions

`integration.execution_wrappers.asyncflow(func=None, *, flow_type,
backend=None, capture_stdio=False, tool_description=None)` registers an async
function with AsyncFlow. `flow_type` is required in practice: without it the
decorator fails with `AttributeError`. Any other keyword raises `TypeError`.
The demo binds it to a short name:

```python
--8<-- "examples/ai-hpc-coupling/agentic_application.py:decorator-alias"
```

`flow_type` selects what the decorated function becomes:

| `AsyncFlowType` | AsyncFlow call | The decorated function | Use it for |
| --- | --- | --- | --- |
| `AGENT_TOOL_AS_FUNCTION` | `flow.function_task` | A LangChain tool whose call awaits the task's result | A tool that an agent or node code calls |
| `FUNCTION_TASK` | `flow.function_task` | An async function that awaits the task's result | A task called from node code |
| `SERVICE_TASK` | `flow.function_task(..., service=True)` | An async function that returns the task's future; awaiting the future gives the service | A resident service, such as a loaded model |
| `EXECUTION_BLOCK` | `flow.block` | An async function of the graph state | A graph node run as an AsyncFlow block |

`backend` places a task on a named backend; `EXECUTION_BLOCK` does not pass it
to AsyncFlow. `tool_description` is the tool's description for the model.
Each call runs once, with no retry and no timeout; see
[Retries and timeouts](retries-and-timeouts.md).

### A resident service

```python
--8<-- "examples/ai-hpc-coupling/agentic_application.py:service-task"
```

The first `await` returns the service task's future; the second returns the
service. See [Services](../features/services.md).

### Agent tools

```python
--8<-- "examples/ai-hpc-coupling/agentic_application.py:agent-tools"
```

`run_simulation` runs on `compute`, the other tools on `ai`. The executor
below retries a transient simulation failure once.

### Tasks

```python
--8<-- "examples/ai-hpc-coupling/agentic_application.py:function-task"
```

### Graph nodes

```python
--8<-- "examples/ai-hpc-coupling/agentic_application.py:block-nodes"
```

`add_instrumented_nodes` adds each node to the graph wrapped for the node
report; see [Telemetry](../features/telemetry.md).

### Calling tools from node code

The demo's nodes call their tools directly with `.ainvoke`, so the tools run as
AsyncFlow tasks without an LLM choosing them. The executor runs one simulation
per approved candidate, concurrently:

```python
--8<-- "examples/ai-hpc-coupling/agentic_application.py:tool-ainvoke"
```

## Let a model select a tool

An `AGENT_TOOL_AS_FUNCTION` tool also works in LangGraph's `ToolNode`, where
the model chooses it. The substitution tests build this graph:

```python
--8<-- "tests/integration/test_substitution.py:agent-graph"
```

and give it the demo's simulation as a tool, through Flowgentic or directly:

```python
--8<-- "tests/integration/test_substitution.py:science-tool"
```

With a scripted model that calls `run_simulation`, the tool runs as one
AsyncFlow task on `compute` and returns its result to the model
(`test_llm_selected_tool_runs_as_an_asyncflow_task`).

LangGraph 1.x's `ToolNode` re-raises tool errors by default, where 0.6
returned them to the model as messages. A tool that fails aborts the graph
run, unless the application passes `handle_tool_errors=True` to `ToolNode`.

## Hand off to another agent

`integration.execution_wrappers.create_task_description_handoff_tool(agent_name,
description)` returns a tool named `transfer_to_<agent_name>`. When a
supervisor agent calls it with a task description, an AsyncFlow task builds
`Command(goto=[Send(agent_name, ...)], graph=Command.PARENT)`, which sends the
graph state, with the task description as its only message, to the
`agent_name` node of the parent graph. The graph state can be a Pydantic model
or a TypedDict:

```python
--8<-- "tests/integration/test_substitution.py:handoff-tool"
```

`test_supervisor_hands_off_to_a_worker` runs the handoff from a supervisor
subgraph to a `researcher` node, for both kinds of state. From October 2025
until 0.3.0, this tool failed on every call on `main`.

## Chat models

`flowgentic.utils.llm_providers.ChatLLMProvider(provider, model=..., ...)`
builds a LangChain chat model:

| `provider` | Model class | Credentials |
| --- | --- | --- |
| `"openrouter"` | `ChatOpenRouter`, a `ChatOpenAI` pointed at `https://openrouter.ai/api/v1` | `api_key=`, or the `OPEN_ROUTER_API_KEY` environment variable |
| `"chatopenai"` or `"chatgpt"` | `ChatOpenAI` | As for `langchain-openai` |
| `"ollama"` | `ChatOllama` | None |

The provider name is case-insensitive, and other keyword arguments go to the
model class. `tests/unit/test_llm_providers.py` builds each one offline.

## Things to know

- Flowgentic retries nothing. A graph node takes LangGraph's `retry_policy`
  and `timeout`, and a task call is wrapped by the application; see
  [Retries and timeouts](retries-and-timeouts.md).
- Importing Flowgentic configures no logging. `flowgentic.configure_logging()`
  prints its records; see [Logging](../features/logging.md).
- [Project status](../status.md) lists what is not validated, such as remote
  and HPC backends.
