# Fault tolerance

Every function decorated with `integration.execution_wrappers.asyncflow` runs
inside a retry loop: tools, tasks, services and graph nodes alike. The loop
retries an attempt that fails with a retryable error, after an exponential
back-off with jitter, and cancels an attempt that outlasts its timeout. The
policy is a `RetryConfig` (`flowgentic.langGraph.fault_tolerance`), given per
function with `retry=`.

## Defaults

Without `retry=`, the decorator uses `RetryConfig()`:

| Field | Default | Meaning |
| --- | --- | --- |
| `max_attempts` | 3 | Attempts, including the first |
| `timeout_sec` | 30.0 | Timeout of each attempt; `None` disables it |
| `base_backoff_sec` | 0.5 | Back-off before the second attempt; it doubles with each attempt |
| `max_backoff_sec` | 8.0 | Upper bound of the back-off |
| `jitter` | 0.25 | The back-off varies at random by up to this fraction |
| `retryable_exceptions` | `()` | Exceptions to retry; empty means the default list below |
| `raise_on_failure` | `True` | Raise after the last attempt; with `False`, return an error dictionary instead |

With `retryable_exceptions` empty, the loop retries `asyncio.TimeoutError`,
`TimeoutError`, `ConnectionError` and `OSError`, plus httpx's `ConnectError`,
`ReadTimeout`, `WriteError` and `RemoteProtocolError` and aiohttp's
`ClientConnectionError` and `ServerTimeoutError`. Any other exception ends the
call at once.

These defaults suit short tool calls, such as a request to a model or a web
service. They do not suit long scientific tasks: an attempt that runs longer
than 30 s is cancelled and started again, and after the third attempt the call
fails with a timeout. The defaults also apply to graph nodes
(`EXECUTION_BLOCK`), so a node that times out or fails with a retryable error
re-runs all the tasks it started. The
[substitution experiment](../evaluation/substitution.md) measured both effects
(row 3): with a simulation that always fails, the default node retry tripled
the simulation attempts, from 8 to 24.

## A policy per function

The agentic demo gives its simulation tool a policy of its own: two attempts,
5 s each, retrying only the demo's transient error:

```python
--8<-- "examples/ai-hpc-coupling/demo_support.py:simulation-retry"
```

and passes it to the decorator:

```python
--8<-- "examples/ai-hpc-coupling/agentic_application.py:agent-tools"
```

In the demo, the first simulation of cycle 1 fails once and is retried; the run
reports `retries=1` for that cycle.

A node that runs long tasks needs its own policy too, for example a
`RetryConfig` with `max_attempts=1` and `timeout_sec=None`, so that it runs
once with no timeout.

## Error payloads

With `raise_on_failure=False`, a call that fails for good returns a dictionary
instead of raising: `tool` (the function's name), `status` (`"error"`),
`attempts`, `retryable`, `error_type` and `error`.

## Without Flowgentic

AsyncFlow 0.6.0 has no per-task retry or timeout. An application that runs
LangGraph on AsyncFlow directly writes its own; see
[LangGraph on AsyncFlow directly](../guides/langgraph-direct.md#4-retries).

## API

[`flowgentic.langGraph.fault_tolerance`](../api/flowgentic/langGraph/fault_tolerance.md)
