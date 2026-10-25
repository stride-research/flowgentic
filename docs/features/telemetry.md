# Telemetry

Flowgentic records a run as OpenTelemetry spans that follow OpenTelemetry's
[GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai).
An agent's decision, the AsyncFlow task it runs in, the model call it makes and
the simulations the run launches form one trace.

Flowgentic depends on `opentelemetry-api` only. Until the application
configures the OpenTelemetry SDK, every span is a no-op.

## What records what

Each layer records what it owns, and Flowgentic records only what no other
layer knows:

| Spans | Recorded by |
| --- | --- |
| `invoke_agent <identity>`, one per call of an agent bound with `bind_agent` | Flowgentic |
| `chat <model>`, `execute_tool <tool>`, `invoke_workflow <graph>` | OpenTelemetry's GenAI instrumentation for LangChain, with its OpenAI instrumentation |
| `session`, `workflow`, `block`, `task` | RHAPSODY, once the application calls `flow.start_telemetry()` |

An `invoke_agent` span is recorded in the caller's process, around the
submission of the agent's task and the wait for it. It carries:

| Attribute | Value |
| --- | --- |
| `gen_ai.operation.name` | `invoke_agent` |
| `gen_ai.agent.name` | The `identity` given to `bind_agent` |
| `flowgentic.task.uid` | The AsyncFlow task's uid, which is the `task_id` of RHAPSODY's `task` span |
| `flowgentic.task.backend` | The backend the task was placed on |
| `error.type` | The exception's class, if the call failed; the span's status is then ERROR |

Flowgentic also carries the caller's trace context into every task and service
it submits. AsyncFlow runs a task's function in a pool thread or in another
process, where the context does not follow by itself. Without it, a model call
made inside a task would start a trace of its own.

## Set it up

The application configures OpenTelemetry. It installs the SDK, turns on the
GenAI instrumentation, and gives RHAPSODY a span processor of its own. The
AI-HPC demo's `tracing.py` does this, and writes every span to a file, one JSON
object per line:

```python
--8<-- "examples/ai-hpc-coupling/tracing.py:run-trace"
```

The runner creates a `RunTrace` before the engine. Then:

1. It calls `attach(flow)` once the engine exists, before the campaign's tasks
   run.
2. It calls `close()` after the engine has shut down.

`engine.shutdown()` does not stop RHAPSODY's telemetry, and RHAPSODY's
`stop()` shuts down the processors it was given, so stop it last. The demo's
runners take the same setup as an option:

```bash
uv run --extra examples python examples/ai-hpc-coupling/augmented_campaign.py --trace spans.jsonl
```

To send spans to a collector or a trace backend such as Jaeger instead, use a
`BatchSpanProcessor` with an OTLP exporter in place of the file.

The packages are in the `examples` extra:
- `opentelemetry-sdk`;
- `opentelemetry-instrumentation-genai-langchain` and
  `opentelemetry-instrumentation-genai-openai`;
- `rhapsody-py[telemetry]`, which AsyncFlow needs for its spans.

## What a trace looks like

The start of the augmented demo's trace, in rehearsal mode, where the agents
make no model calls:

```text
session                        backend=rhapsody
├─ task                        executable=load_surrogate  task_id=task.000001
├─ task                        executable=propose_candidates
├─ invoke_agent planner        flowgentic.task.uid=task.000003  flowgentic.task.backend=ai
│  ├─ task                     executable=agent_planner  task_id=task.000003  backend=compute
│  └─ invoke_workflow LangGraph
├─ task                        executable=simulate   (four, in parallel)
├─ task                        executable=update_surrogate
├─ invoke_agent analyst        …
└─ invoke_agent supervisor     …
```

The planner's LangGraph component runs inside its task, on the `ai` backend,
and its spans nest under the decision. The `task` span's `backend` reads
`compute` because AsyncFlow 0.6.0 labels every task span with the default
backend. Flowgentic's `flowgentic.task.backend` gives the backend the task
actually ran on.

## Token usage

Usage is recorded on each `chat` span, by the instrumentation that saw the
response, as the provider reported it:

| Attribute | Meaning |
| --- | --- |
| `gen_ai.usage.input_tokens` | All input tokens, cached ones included |
| `gen_ai.usage.output_tokens` | All output tokens, reasoning ones included |
| `gen_ai.usage.cache_read.input_tokens`, `gen_ai.usage.cache_write.input_tokens` | Input tokens read from, or written to, the provider's cache |
| `gen_ai.usage.reasoning.output_tokens` | Output tokens spent on reasoning |

A response without usage gives a span without these attributes: a count nobody
measured is absent, never zero. The conventions also omit a cache or reasoning
count that is zero. So a missing breakdown means "zero or unknown", while a
missing input or output total means "not measured".

Flowgentic records no token counts itself, and adds none up on its own spans.
A decision's total is the sum over the `chat` spans below its `invoke_agent`
span.

Two sources still invent counts, and Flowgentic cannot detect either from the
client side:
- langchain-openai records 0 for a response whose `usage` lacks
  `prompt_tokens` or `completion_tokens`.
- RHAPSODY's Dragon-vLLM endpoint reports whitespace word counts as tokens.

## Message content

Prompts, outputs, tool arguments and tool results are not recorded unless
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` is set to `SPAN_ONLY`,
`EVENT_ONLY` or `SPAN_AND_EVENT`, as the GenAI instrumentation reads it.
Flowgentic's own spans carry no content, and neither do its log records; see
[Logging](logging.md).

## Cost

OpenTelemetry has no cost attribute yet, so Flowgentic records no cost.
`examples/telemetry/cost.py` is a recipe that computes it from a span file. It
prices each inference span in one of three ways:

- **The cost the provider reported**, when the span carries one.
  OpenRouter reports one, but no instrumentation records it yet.
- **Usage times the price in force**, for a model behind a priced API. The
  caller gives the prices. Pydantic's [genai-prices](https://github.com/pydantic/genai-prices)
  keeps them current and historic, with
  `calc_price(Usage(...), model_ref=..., provider_id=..., genai_request_timestamp=...)`.
- **A share of the GPU-hours**, for a model served on the allocation, such as
  vLLM.
    - Each deployment's GPU-hours over the campaign, as the batch system
      accounts them, idle time included, are split over its calls by the input
      and output tokens the server counted.
    - Calls are grouped by `server.address`. The OpenAI instrumentation adds
      that attribute to the `chat` span.

With `examples/telemetry` on the path:

```python
from pathlib import Path

from cost import Price, allocation_shares, inference_spans, priced_cost

spans = inference_spans(Path("spans.jsonl"))
remote = [s for s in spans if s["attributes"].get("server.address") == "openrouter.ai"]
price = Price(input=0.3, output=2.5)  # US dollars per million tokens
costs = [priced_cost(span, price) for span in remote]
unmeasured = costs.count(None)
dollars = sum(cost for cost in costs if cost is not None)
gpu_hours = allocation_shares(spans, {"gpu-node-1": 6.0})  # by span id
```

A call without measured usage gets no cost and no share, so report how many
there were beside the total. It is not counted as zero.

## Conventions version

The GenAI conventions are at Development status, and their repository has no
release yet. Flowgentic's names follow
[`semantic-conventions-genai`](https://github.com/open-telemetry/semantic-conventions-genai)
at commit `9ffc7e0` (2026-10-21). The `examples` and `dev` extras take the
LangChain and OpenAI instrumentation 1.3b0, below 1.4: each beta emits the
latest names. A change of conventions changes Flowgentic's names in a release,
and the changelog lists each one.

## Limitations

- A model call inside a task is a sibling of RHAPSODY's `task` span, under the
  caller's span, not its child: RHAPSODY creates the task span after the call,
  so the caller cannot hand it to the task.
- An AsyncFlow block's span hangs from RHAPSODY's `session` span, not from the
  span that called the block.
- AsyncFlow 0.6.0 labels every `task` span with the default backend.
- No cost reported by a provider is recorded.
- Without the OpenAI instrumentation, `chat` spans carry no `server.address`.

In 0.3, Flowgentic wrote a Markdown node report, `execution_summary.md`, rebuilt
from the graph's final messages. It saw no tasks, retries or rejected
proposals, and summed token counts that the demo had set to zero. The spans
replace it; see the [changelog](../changelog.md).

## API

[`flowgentic.agent`](../api/flowgentic/agent.md)
