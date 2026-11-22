# Telemetry

Flowgentic records a run as OpenTelemetry spans and events that follow
OpenTelemetry's
[GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai).
An agent's decision, the AsyncFlow task it runs in, the model call it makes,
the checks its output passed or failed, and the simulations the run launches
form one trace. Agents run at scale, by an [agent host](agent-host.md), are
also counted, in [metrics](#metrics): what the agents are doing, the host's
own health, the tools' durations and the model fleet's load.

Flowgentic depends on `opentelemetry-api` only. Until the application
configures the OpenTelemetry SDK, every span, event and metric is a no-op.

## What records what

Each layer records what it owns, and Flowgentic records what no other layer
knows. One of its spans, `execute_tool`, shares its name with a span of the
LangChain instrumentation; see the end of this section.

| Spans and events | Recorded by |
| --- | --- |
| `invoke_agent <identity>`, one per call of an agent bound with `bind_agent` | Flowgentic |
| `invoke_agent`, one per agent an agent host runs, with a `flowgentic.agent.suspend` event per suspension; see [the agent host's spans](#the-agent-hosts-spans) | Flowgentic |
| `execute_tool <tool>`, one per call of a task or MCP tool from `flowgentic.tools`, and of an inline tool on the [agent loop](agent-loop.md); see [Tool placement](tools.md#what-it-reports) | Flowgentic |
| `chat <model>`, one per model call through the fleet, its wait for admission, tries and outages included, or through the agent loop's transport, with the usage the server reported; see [Fleet](fleet.md#what-it-reports). With LangChain replicas and the LangChain instrumentation on, the instrumentation's `chat` span takes its place | Flowgentic |
| `accept <name>`, one per call of `accept`, with an `accept.attempt <name>` span per attempt and a `gen_ai.evaluation.result` event per check that ran; see [Acceptance](acceptance.md) | Flowgentic |
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

With the LangChain instrumentation on, a call of a task or MCP tool has both
LangChain's `execute_tool` span, one per run of the LangChain tool (two when
the task suspends its agent), and Flowgentic's, with the same
`gen_ai.tool.call.id`. Flowgentic's adds where the call ran
(`flowgentic.tool.kind`, `task` or `service`), how it ended
(`flowgentic.tool.outcome`, which tells a refused or cancelled task from a
failed one), and the call's whole duration, from submission through the order
policy's wait and the agent's suspension to the result, with the spans of the
task's function under it.

### The agent host's spans

An [agent host](agent-host.md) records each agent's run as one
`invoke_agent` span, from the agent's start to its end, suspensions included.
The span's name carries no agent id, which would make as many span names as
agents; the id is an attribute:

| Attribute | Value |
| --- | --- |
| `gen_ai.operation.name` | `invoke_agent` |
| `gen_ai.agent.id` | The agent's id, its thread id for LangGraph |
| `error.type` | The exception's class if the agent failed, `CancelledError` if it was cancelled; the span's status is then ERROR |

A failed agent's span also records its exception, as an `exception` event,
and its status description is the error. An interrupted agent's span, such as
one that asks a human, is not an error. Each suspension is a
`flowgentic.agent.suspend` event, emitted while the span is current, with
`gen_ai.agent.id` and `flowgentic.agent.suspend.waits`, the number of tasks
the agent waits on. The spans of the agent's model calls and tools nest under
its `invoke_agent` span.

## Metrics

Flowgentic's metrics describe agents run at scale. Their names are
Flowgentic's own, under `flowgentic.`: the GenAI conventions have none for
agents' states or an agent process's health. Every duration is in seconds.

| Metric | Instrument | Unit | Attributes | What it measures |
| --- | --- | --- | --- | --- |
| `flowgentic.agents` | Gauge, read at collection | `{agent}` | `flowgentic.agent.state` | Agents in each state, summed over the process's running agent hosts |
| `flowgentic.agent.time` | Counter, read at collection | `s` | `flowgentic.agent.state`, `flowgentic.agent.category` | Agent-seconds spent in each state by every agent the process has run |
| `flowgentic.host.loop.lag` | Histogram | `s` | | How late the event loop woke a 100 ms timer, sampled every 100 ms while a host runs on it |
| `flowgentic.host.gc.pause` | Histogram | `s` | `flowgentic.gc.generation` | Each pause of the cyclic garbage collector while a host runs |
| `flowgentic.tool.duration` | Histogram | `s` | `gen_ai.tool.name`, `flowgentic.tool.kind`, `flowgentic.tool.outcome` | Time from a task or service call's submission to its result; see [Tool placement](tools.md#what-it-reports) |
| `flowgentic.fleet.admission.wait` | Histogram | `s` | `flowgentic.fleet.name`, `flowgentic.fleet.replica` | Time a model request waited for its replica's admission |
| `flowgentic.fleet.request.duration` | Histogram | `s` | `flowgentic.fleet.name`, `flowgentic.fleet.replica` | Time a model request spent at its replica, after admission |
| `flowgentic.fleet.call.duration` | Histogram | `s` | `flowgentic.fleet.name`, `flowgentic.fleet.replica`, `error.type` | Time a model call took through the fleet: its wait for admission, its tries and any outage it waited out; one per call, as its `chat` span |
| `flowgentic.fleet.retries` | Counter | `{request}` | `flowgentic.fleet.name`, `flowgentic.fleet.replica`, `error.type` | Requests sent again once their replica came back, by the error before each |
| `flowgentic.fleet.outage.duration` | Histogram | `s` | `flowgentic.fleet.name`, `flowgentic.fleet.replica`, `flowgentic.fleet.outage.outcome` | Each outage `Recovery` watched, from the call that found the replica down to its return (`recovered`) or to `Recovery` giving up (`gave_up`) |
| `flowgentic.fleet.tokens` | Counter, read at collection | `{token}` | `flowgentic.fleet.name`, `flowgentic.fleet.replica`, `gen_ai.token.type` (`input`, `output`) | The prompt and completion tokens the servers counted for the fleets' calls; a fleet that is gone keeps its count |
| `flowgentic.fleet.tokens.active` | Gauge, read at collection | `{token}` | `flowgentic.fleet.name`, `flowgentic.fleet.replica` | Tokens of live context in flight at each replica, as admitted |
| `flowgentic.fleet.requests.active` | Gauge, read at collection | `{request}` | `flowgentic.fleet.name`, `flowgentic.fleet.replica` | Requests in flight at each replica |
| `flowgentic.fleet.requests.waiting` | Gauge, read at collection | `{request}` | `flowgentic.fleet.name`, `flowgentic.fleet.replica` | Requests waiting for each replica's admission |

`flowgentic.agent.state` is one of `pending`, `working`, `tool_call`,
`model_queued`, `model_request`, `tool_wait` and `done`, the
[agent host's states](agent-host.md#what-it-reports) in lower case.
`flowgentic.agent.category` is `working`, `waiting`, `model_request`, or
`none` for `pending` and `done`. `flowgentic.agents` counts the agents of
runs in progress only: once a host's `run` returns, its agents leave the
counts, and with no run in progress every state reads 0.
`flowgentic.agent.time` only grows: a new run, on the same host or another,
adds to it, and it keeps the time of hosts that are gone. A run's own
agent-seconds are in its report. These instruments cost nothing while agents
run: the SDK reads the counts when it collects.

Hosts that run at the same time in a process share one collector callback
and, on each event loop, one lag timer, so each pause and each lag sample is
recorded once. The collector's pauses are recorded from the event loop, at
the next lag sample or when a host stops, never inside a collection: a
collection can start while the SDK holds a histogram's lock, and recording
there would wait for ever.

Each histogram advises the SDK of bucket boundaries in seconds, sized for what
it measures. The SDK's default boundaries, from 0 to 10,000, suit
milliseconds.

| Histogram | Boundaries, in seconds |
| --- | --- |
| `flowgentic.host.loop.lag` | 0.001 to 10: 1, 2.5 and 5 in each decade |
| `flowgentic.host.gc.pause` | 0.0001 to 10: 1, 2.5 and 5 in each decade |
| `flowgentic.tool.duration` | 0.001 to 10 as above, then 30, 60, 120, 300, 600, 1800, 3600 and 7200 |
| `flowgentic.fleet.admission.wait` | 0.001 to 10 as above, then 30, 60, 120, 300 and 600 |
| `flowgentic.fleet.request.duration` | 0.01, doubling to 655.36: the GenAI conventions' boundaries for a model call's duration, three doublings further |
| `flowgentic.fleet.call.duration` | As a request's, two doublings further, to 2621.44 |
| `flowgentic.fleet.outage.duration` | 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600 and 7200 |

A view the application configures for a histogram replaces its boundaries.

### Configure a meter provider

The application configures metrics as it configures traces: it installs
`opentelemetry-sdk` and sets a meter provider with a reader, once per
process, before or after it imports Flowgentic. This writes every metric to
a file every 10 seconds, one JSON object per export:

```python
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)

out = open("metrics.jsonl", "w")
exporter = ConsoleMetricExporter(
    out=out, formatter=lambda data: data.to_json(indent=None) + "\n"
)
reader = PeriodicExportingMetricReader(exporter, export_interval_millis=10_000)
provider = MeterProvider(metric_readers=[reader])
metrics.set_meter_provider(provider)

...  # run the agent hosts

provider.shutdown()  # exports what is left
out.close()
```

To send metrics and spans to a collector instead, for a dashboard that shows
the run while it goes, the `otlp` extra's `flowgentic.otlp.configure()` sets
up the SDK in one call; see [Live dashboard](live-dashboard.md). In a
campaign of several agent processes, each exports its own metrics: give each
provider a `Resource` with a `service.instance.id` of its own, so that a
backend can tell the processes apart.

### Read an export back

In a campaign of several agent processes, each process can write its export
into a directory of its own: its metrics as above, and its spans one per
line. `flowgentic.telemetry.read_export` reads them all back, merged, and
checks them.

Each process configures the SDK like this, with `out` its own directory,
such as `run/otel/p003`. This is the configuration for thousands of agents,
which keeps the SDK's cost down; see
[what it costs](#what-it-costs-and-how-to-cut-it):
- spans sampled per agent: each agent's `invoke_agent` span is the root of
  its trace, so `ParentBased(TraceIdRatioBased(0.1))` keeps a tenth of the
  agents, each with its whole trace;
- no exemplars, which the SDK otherwise keeps for every value recorded
  inside a sampled span;
- metrics exported every 10 s or less often.

```python
from pathlib import Path

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import AlwaysOffExemplarFilter, MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

out = Path("run/otel/p003")
out.mkdir(parents=True, exist_ok=True)
resource = Resource.create({"service.instance.id": out.name})
metrics_file = (out / "metrics.jsonl").open("w")
reader = PeriodicExportingMetricReader(
    ConsoleMetricExporter(
        out=metrics_file, formatter=lambda data: data.to_json(indent=None) + "\n"
    ),
    export_interval_millis=10_000,
)
meter_provider = MeterProvider(
    resource=resource,
    metric_readers=[reader],
    exemplar_filter=AlwaysOffExemplarFilter(),
)
metrics.set_meter_provider(meter_provider)
spans_file = (out / "spans.jsonl").open("w")
tracer_provider = TracerProvider(
    resource=resource, sampler=ParentBased(TraceIdRatioBased(0.1))
)
tracer_provider.add_span_processor(
    BatchSpanProcessor(
        ConsoleSpanExporter(
            out=spans_file,
            formatter=lambda span: span.to_json(indent=None) + "\n",
        ),
        max_queue_size=65_536,  # the default, 2,048, drops spans in a burst
    )
)
trace.set_tracer_provider(tracer_provider)

...  # run the agent hosts

meter_provider.shutdown()
tracer_provider.shutdown()
metrics_file.close()
spans_file.close()
```

Then, once the processes have ended:

```python
from flowgentic.telemetry import read_export

export = read_export("run/otel")  # every metrics.jsonl and spans.jsonl below
problems = export.check(sampled=True)
# The servers' own counters over the run, as the application sampled them
problems += export.check_servers(
    prompt_tokens=prompt_tokens, completion_tokens=generated_tokens, requests=successes
)
print(export.calls, export.fleet_usage, export.agents[-1])
```

An `Export` sums Flowgentic's counts over the processes, each at its last
export: the fleets' calls and failed calls, the requests the replicas got,
the requests sent again, the outages waited out or given up and their time,
the agent-seconds by state, the tokens the servers counted for the fleets'
calls (`fleet_usage`, from `flowgentic.fleet.tokens`), and the `chat` spans
with the usage of those that did not fail (`usage`). `export.agents` is the
agents in each state over time: at each export's time, each process's latest
counts summed.

`check()` lists what is missing or does not add up:
- a directory with no `metrics.jsonl`, or one with no Flowgentic metrics;
- in a process that wrote spans, the fleet's `chat` spans against its calls,
  failed ones apart. The fleet records one span per call, so a gap means
  spans were dropped, as a `BatchSpanProcessor` whose queue is full drops
  them, or were never exported. With `sampled=True`, for spans sampled as
  above, fewer spans than calls is expected, and only more spans than calls
  is a problem.

`check_servers()` compares the fleets' own counts with the model servers'
counters over the run, such as the differences in vLLM's
`prompt_tokens_total`, `generation_tokens_total` and
`request_success_total` between the first and the last sample: the prompt
and completion tokens, and the successful requests. The counts come from
the metrics, so sampled spans change nothing. An export without
`flowgentic.fleet.tokens`, or whose calls went outside a fleet, is compared
through its `chat` spans. Only what is given is compared.

`read_export` reads the SDK's console JSON with cumulative temporality, the
SDK's default. It raises `ValueError` for a metric exported with delta
temporality, and for a line that is not JSON, as a process killed while
writing leaves one, naming the file and line. It does not read the OTLP JSON
that an OpenTelemetry collector's file exporter writes.

### What it costs and how to cut it

Without an SDK, every instrument is a no-op. With one, the agents' states
cost nothing while they change, since the SDK reads them when it collects,
and so do the fleets' tokens. Everything else costs a little per value or
span:

- each value recorded: about 3 µs in a tight loop, and about 20 µs in a busy
  agent process. Agents on the loop record about 12 values each: per model
  call, its admission wait, its time at the replica and its whole time; per
  task or MCP tool call, its duration;
- each span: about 38 µs kept and written as a JSON line, half of it the
  export, in a tight loop, and about 200 µs in a busy process; a span that
  sampling drops, about 5 µs in a tight loop.

The garbage collector is not where it goes: its pauses barely change with
metrics, and with every span kept they account for about a tenth of the
cost. It is the SDK's own Python, on the agents' event loop and in the span
exporter's thread.

Measured with the benchmarks' `--otel` options (22 Nov 2026, AMD EPYC 7502P,
median of 5 to 7 runs, the whole process's CPU, the SDK's threads and last
flush included):

| SDK | The agent host alone, 100,000 agents (`agent_host.py`) | 5,000 agents on the loop, with model calls and tools (`agent_loop.py`) |
| --- | --- | --- |
| Metrics, every second | +3.1 % | +6 % |
| Metrics, every 10 s, no exemplars | +1.3 % | +8 % |
| Every span, exemplars, metrics every second | about +430 % | +51 % |
| Every span, no exemplars, metrics every 10 s | not measured | +47 % |
| The configuration above: a tenth of the agents' spans, no exemplars, metrics every 10 s | +102 % | +17 % |

The host alone runs agents that do nothing, so its spans weigh far more
there than in any real campaign. A quarter of the loop's spans are the MCP
SDK's own, which sampling drops with their agent.

The levers, biggest first:
1. **Sample spans per agent**, as configured above. A kept agent keeps its
   whole trace; `check(sampled=True)` and `check_servers()` still check the
   counts, from the metrics.
2. **Exemplars off** (`exemplar_filter=AlwaysOffExemplarFilter()`, or
   `OTEL_METRICS_EXEMPLAR_FILTER=always_off`). The SDK's default keeps an
   exemplar for each value recorded inside a sampled span, and every value
   an agent records is inside its `invoke_agent` span.
3. **Export metrics every 10 to 60 s**, not every second. Each export is a
   fixed cost, whatever the agents: it shows on a host whose agents do
   little, and hardly where they call models.
4. **Write JSON lines.** An OTLP exporter is not cheaper in the agents'
   process: the Python exporter builds the protobuf there, at about the cost
   of the JSON line, for half the bytes. An OpenTelemetry collector takes the
   work only after encoding. Use OTLP and a collector for
   [live dashboards](live-dashboard.md) or a trace backend, not to save CPU.

Fewer values per model call would save more: each costs the same, and a
View that drops an instrument saves almost nothing. Only not recording
does.

## Set it up

The application configures OpenTelemetry. It installs the SDK, turns on the
GenAI instrumentation, gives RHAPSODY a span processor of its own, and
configures a logger provider for the events, which are log records. The
AI-HPC demo's `tracing.py` does this, and writes every span and event to a
file, one JSON object per line:

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
`BatchSpanProcessor` with an OTLP exporter in place of the file, as
`flowgentic.otlp.configure()` does (see [Live dashboard](live-dashboard.md)),
and a `BatchLogRecordProcessor` with an OTLP log exporter for the events.

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
│  ├─ task                     executable=agent_planner  task_id=task.000003  backend=ai
│  └─ invoke_workflow LangGraph
├─ task                        executable=simulate   (four, in parallel)
├─ task                        executable=update_surrogate
├─ invoke_agent analyst        …
└─ invoke_agent supervisor     …
```

The planner's LangGraph component runs inside its task, on the `ai` backend,
and its spans nest under the decision. The `task` span's `backend` and
Flowgentic's `flowgentic.task.backend` both name the backend the task ran on.
AsyncFlow 0.6.0 labelled every task span with the default backend, here
`compute`; 0.7 names the right one.

## Token usage

Usage is recorded on each `chat` span, by Flowgentic for a call through the
fleet or the agent loop's transport, or by the instrumentation that saw the
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

Flowgentic adds no counts up on its own spans: a decision's total is the sum
over the `chat` spans below its `invoke_agent` span. A model call has one
`chat` span, Flowgentic's or the LangChain instrumentation's, never both. A
call through the fleet that was sent again, after its replica's outage, is
still one span, with the usage of the try that succeeded.

Two sources still invent counts, and Flowgentic cannot detect either from the
client side:
- langchain-openai records 0 for a response whose `usage` lacks
  `prompt_tokens` or `completion_tokens`.
- RHAPSODY's Dragon-vLLM endpoint reports whitespace word counts as tokens.

## Message content

Prompts, outputs, tool arguments and tool results are not recorded unless
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` is set to `SPAN_ONLY`,
`EVENT_ONLY` or `SPAN_AND_EVENT`, as the GenAI instrumentation reads it.
Flowgentic reads the same switch. Its only content is the value an
acceptance check rejected, which goes on the attempt's span under `SPAN_ONLY`
or `SPAN_AND_EVENT`; see [Acceptance](acceptance.md#telemetry). Its
`logging` records carry no content; see [Logging](logging.md).

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
- No cost reported by a provider is recorded.
- Without the OpenAI instrumentation, `chat` spans carry no `server.address`.

In 0.3, Flowgentic wrote a Markdown node report, `execution_summary.md`, rebuilt
from the graph's final messages. It saw no tasks, retries or rejected
proposals, and summed token counts that the demo had set to zero. The spans
replace it; see the [changelog](../changelog.md).

## API

- [`flowgentic.agent`](../api/flowgentic/agent.md)
- [`flowgentic.otlp`](../api/flowgentic/otlp.md)
- [`flowgentic.telemetry`](../api/flowgentic/telemetry.md)
