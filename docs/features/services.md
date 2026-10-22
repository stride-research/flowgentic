# Services

A service is a long-lived object that a workflow starts once and then uses
many times: a loaded model, a simulator, a connection pool. AsyncFlow runs it
as a service task (`function_task(..., service=True)`), and Flowgentic's
`SERVICE_TASK` flow type registers one through `LangraphIntegration`.

## Start a service

Decorate the function that starts the service. This example's service is a
small simulated server, `examples/langgraph-integration/service-task/utils/simple_server.py`;
it opens no network port:

```python
--8<-- "examples/langgraph-integration/service-task/service-intermittent.py:service-task"
```

Calling the decorated function submits the service task and returns its
future. Awaiting that future gives the service, and later requests go to the
same running instance:

```python
--8<-- "examples/langgraph-integration/service-task/service-intermittent.py:service-handle"
```

Each call to the decorated function starts a new instance, with its own state:

```python
--8<-- "examples/langgraph-integration/service-task/service-intermittent.py:service-new"
```

Cancel the future to stop the service task, and release the service's own
resources:

```python
--8<-- "examples/langgraph-integration/service-task/service-intermittent.py:service-cleanup"
```

Run the example from the repository root:

```bash
uv run --extra examples python -m examples.langgraph-integration.service-task.service-intermittent
```

It starts two servers, sends four requests to the first and two to the second,
and reports one cold start per server:

```text
======================================================================
✅ PROOF: Both services running independently
======================================================================
   Server 1: 4 total requests (counter at 4)
   Server 2: 2 total requests (counter at 2)
   Both maintained separate state and uptime
```

`tests/integration/test_feature_examples.py` runs the example and checks these
counts.

## In the agentic demo

The agentic demo loads its surrogate model as a service on the `ai` backend,
and its tools use that one instance for every request:

```python
--8<-- "examples/ai-hpc-coupling/agentic_application.py:service-task"
```

The demo's summary records one load and five requests (`"loads": 1`,
`"requests": 5`). The runner cancels the service's future when the campaign
ends.

## Service or task

| | `FUNCTION_TASK` | `SERVICE_TASK` |
| --- | --- | --- |
| AsyncFlow call | `function_task` | `function_task(..., service=True)` |
| Calling the decorated function returns | The task's result | The task's future; awaiting it gives the service |
| Each call | Runs the function | Starts a new service instance |
| Stop it | It ends when the function returns | Cancel the future |

Both go through the retry loop of [Fault tolerance](fault_tolerance.md). For a
service, the loop covers submitting the task, not the service's own start-up.

Without Flowgentic, the same service is `@flow.function_task(service=True)`; see
[LangGraph on AsyncFlow directly](../guides/langgraph-direct.md#2-services-and-tasks).

## API

[`flowgentic.langGraph.execution_wrappers`](../api/flowgentic/langGraph/execution_wrappers.md)
