# Retries and timeouts

Flowgentic runs each decorated call once. It adds no retry and no timeout to
tools, tasks, services or graph nodes. Each kind of failure has an owner that
can tell whether it is transient and can redo only the work that failed:

| What fails | Who retries it | Who sets its deadline |
| --- | --- | --- |
| A model call: a rate limit, a server error, a lost connection | The provider SDK, through the chat model's `max_retries`; OpenAI's SDK retries twice by default. An agent built with `create_agent` can add `ModelRetryMiddleware` and `ModelFallbackMiddleware` | The chat model's `timeout` |
| A tool in an agent loop | `ToolRetryMiddleware`, or the application around the tool call | The tool |
| A graph node | LangGraph: `add_node(..., retry_policy=RetryPolicy(...))` | LangGraph: `add_node(..., timeout=...)`, from LangGraph 1.2 |
| An AsyncFlow task | The application: AsyncFlow 0.6.0 has no per-task retry | The application: AsyncFlow 0.6.0 has no per-task timeout |

`ToolNode(handle_tool_errors=...)` does not retry a tool. It returns the error
to the model as a `ToolMessage`, and the model decides whether to call the
tool again.

## Why there is no default

- A graph node groups tasks, and retrying it re-runs the ones that finished.
  In the [substitution experiment](../evaluation/substitution.md), the default
  node retry of Flowgentic 0.3 turned 8 simulation attempts into 24.
- Only the layer that sees a failure can tell whether it is transient: the SDK
  knows a rate limit, and the application knows its simulation's errors. A
  list of retryable exceptions chosen in advance also catches errors that will
  fail again, such as a missing input file.
- A deadline needs the work's expected duration, which only the application
  knows. The 30 s default of 0.3 failed any node whose work took longer.
- Retries at several layers multiply: each layer repeats every attempt of the
  layers below it.

## A graph node

LangGraph's node policies work on a Flowgentic block node as on any async
node. A retry re-runs every task in the node, so give a node a retry policy
only when its tasks are cheap or safe to repeat:

```python
--8<-- "tests/unit/test_execution_wrappers.py:node-retry-policy"
```

`retry_on` names the transient errors. Without it, LangGraph retries any
exception except a list of programming errors and the `OSError`s other than
`ConnectionError`; see `langgraph.types.RetryPolicy`.

From LangGraph 1.2, `add_node(..., timeout=3600)` caps each attempt's
wall-clock time. An attempt that exceeds it raises
`langgraph.errors.NodeTimeoutError`, which the retry policy may retry.
`TimeoutPolicy(idle_timeout=...)` instead fails an attempt that shows no
progress for that long. A timeout cancels the node and the AsyncFlow tasks it
is awaiting; that cancellation is validated on local backends only.

## An AsyncFlow task

AsyncFlow 0.6.0 has no per-task retry or timeout, so the application wraps the
calls that need them. The AI-HPC demos retry a simulation once after a
transient failure, with 5 s per attempt:

```python
--8<-- "examples/ai-hpc-coupling/campaign_common.py:retry-transient"
```

and wrap each simulation call in it:

```python
--8<-- "examples/ai-hpc-coupling/agentic_application.py:tool-ainvoke"
```

The RADICAL-only baseline and the [direct version](langgraph-direct.md#4-retries)
use the same helper, so all three versions retry the same simulations. In the
demos, the first simulation of the second cycle fails once and is retried; the
run prints `retries=1` on the `cycle= 2` line.

## A model call

The chat model retries its own requests and enforces its own deadline. With
`ChatLLMProvider`, keyword arguments go to the model class, so
`ChatLLMProvider("openrouter", model=..., max_retries=2, timeout=120)` sets
both on the `ChatOpenAI` it builds.

## A service

A `SERVICE_TASK` starts one service per call. Flowgentic does not restart a
service that fails; the application decides whether to start another.

## Changes from 0.3

Up to 0.3, every decorated call ran in a retry loop, configured with
`RetryConfig` from `flowgentic.langGraph.fault_tolerance`: by default 3
attempts of at most 30 s each. 0.4.0 removes the loop, the module and the
decorator's `retry=` argument; see the [changelog](../changelog.md).
