# Live dashboard

A run's telemetry can be watched while the run goes: the agents in each state,
the model calls and tokens per second, the requests waiting at each replica,
the tools, and each agent process's event loop. The `otlp` extra streams each
agent process's metrics and spans to an
[OpenTelemetry collector](https://opentelemetry.io/docs/collector/) over OTLP,
and `examples/live-dashboard` starts a collector, Prometheus, Jaeger and
Grafana, with dashboards, in one command.

![Flowgentic's dashboard during a run of 12,000 agents in 4 processes](../assets/live-dashboard.png)

The stream comes beside the per-process files that
[`read_export`](telemetry.md#read-an-export-back) reads, not in their place:
the files are a run's record, checked once it ends; the stream is for
watching it. `configure()` can write both.

## Start the dashboard

With Docker:

```bash
docker compose -f examples/live-dashboard/compose.yaml up -d
```

Then open <http://localhost:3000>. Every port is bound to 127.0.0.1:

| Port | Service |
| --- | --- |
| 3000 | Grafana, with Flowgentic's dashboard as its home page; viewers need no login, `admin`/`admin` edits |
| 4318 | The collector's OTLP/HTTP receiver, what `flowgentic.otlp` sends to |
| 4317 | The collector's OTLP/gRPC receiver |
| 9090 | Prometheus |
| 16686 | Jaeger, the sampled traces |

`docker compose -f examples/live-dashboard/compose.yaml down` stops it; with
`-v` it also drops Prometheus's and Grafana's data. Jaeger keeps its traces
in memory only.

## Stream a run to it

Install the extra, `flowgentic[otlp]`, which adds `opentelemetry-sdk` and its
OTLP/HTTP exporter. Each agent process calls `configure()` once, before its
agent hosts run, as the agents-at-scale example does with `--otlp`:

```python
--8<-- "examples/agents-at-scale/campaign.py:otlp"
```

`configure()` sets the process's meter and tracer providers, so everything
that records through OpenTelemetry's global providers goes to the collector:
Flowgentic's metrics and spans, and the MCP SDK's and the GenAI
instrumentation's spans. RHAPSODY keeps providers of its own, so its session
gets a reader and a span processor of the same configuration:

```python
await session.start_telemetry(**telemetry.rhapsody_options())
```

AsyncFlow's `WorkflowEngine.start_telemetry()` takes the same options. When
the run ends, `telemetry.shutdown()` exports what is left.

The example runs a campaign long enough to watch, on a laptop:

```bash
uv run --extra agents --extra otlp python examples/agents-at-scale/campaign.py \
    --agents 12000 --processes 4 --otlp --model-latency 0.5
```

`--otlp` takes the collector's URL, `http://localhost:4318` by default, and
`--run` the run's name on the dashboard. `--model-latency` makes each scripted
model call take that long, here half a second, so the fleet's admission holds
most agents queued: about 2.5 minutes for 36,000 calls.

### What `configure()` sets

The defaults are the [telemetry page's configuration at scale](telemetry.md#what-it-costs-and-how-to-cut-it),
with metrics every 5 s rather than 10 s, for a live view; see
[what it costs](#what-it-costs):

| Argument | Default | What it sets |
| --- | --- | --- |
| `endpoint` | OpenTelemetry's `OTEL_EXPORTER_OTLP_ENDPOINT`, then `http://localhost:4318` | The collector's OTLP/HTTP base URL; the exporters add `/v1/metrics` and `/v1/traces` |
| `run` | `OTEL_RESOURCE_ATTRIBUTES` | `service.namespace`: the run, or campaign |
| `service` | `OTEL_SERVICE_NAME`, then `flowgentic` | `service.name`: the process's role in the run, such as `agents` |
| `instance` | `OTEL_RESOURCE_ATTRIBUTES`, then the host's name and the process id | `service.instance.id`: the process, one per process |
| `sample` | 0.1 | The share of agents whose spans are kept, each with its whole trace: `ParentBased(TraceIdRatioBased(sample))` |
| `exemplars` | `False` | The SDK's default exemplar filter, which keeps an exemplar for each value recorded inside a sampled span |
| `interval_s` | 5 | Seconds between metric exports, the files' too |
| `spans` | `True` | `False` exports metrics only |
| `files` | `None` | A directory for `metrics.jsonl` and `spans.jsonl` as well, as `read_export` reads them |
| `timeout_s` | `OTEL_EXPORTER_OTLP_TIMEOUT`, then 10 | Seconds an export waits for the collector, retries included |

An argument given wins over the environment, which wins over the defaults.
The metrics count every agent and call whatever the sampling, and they are
cumulative: an export that fails, as when the collector is down, is dropped,
and the next one carries the totals. The agents never wait for the
collector; `shutdown()` waits at most `timeout_s` for each signal.

`configure()` refuses a process whose meter or tracer provider is set
already, with `RuntimeError`: the SDK keeps the first provider, and the
telemetry would not reach the collector.

The exporter is OTLP over HTTP with protobuf, OpenTelemetry's default
protocol, rather than gRPC: it needs no `grpcio`, a large native wheel whose
fork handling is a risk with `multiprocessing` and on HPC nodes, and one HTTP
port passes through a tunnel or a proxy.

## A campaign of many agent processes

Each agent process calls `configure()` with the same `run` and an `instance`
of its own, such as its index or its rank, and sends to the same collector.
The dashboards then show the whole run, summed over the processes, or one
process, chosen at the top:

| Variable | Label | From |
| --- | --- | --- |
| Run | `service_namespace` | `run` |
| Process | `service_instance_id` | `instance` |

On an allocation, run the collector where the agent processes can reach it,
such as on the node that starts the campaign, and point their `endpoint` at
it. The collector's configuration, `collector.yaml`, sends metrics to
Prometheus and spans to Jaeger; Prometheus, Jaeger and Grafana can run beside
it or elsewhere, and Grafana is reached through a tunnel to its port. Each
process sends one metrics request per export interval, about 9 kB for the
agents-at-scale example's metrics, and its sampled spans in batches of about
100 kB.

A model server's own metrics, such as vLLM's, go to the same Prometheus:
`collector.yaml` shows a `prometheus` receiver that scrapes a server's
`/metrics`.

## The dashboards

Grafana holds one folder for each component of the stack, and one for the
application:

- **Flowgentic: agents at scale.** The processes, agents running and done,
  model calls, calls and tokens per second, failed calls and requests sent
  again; the agents in each state over time and the agent-seconds by state;
  model calls per second by replica, tokens per second by type, requests in
  flight and waiting at each replica, the admission wait, the time at the
  replica and the whole call (median and 95th percentile), requests sent
  again and outages, and failed calls by error; tool calls per second and
  the tools' 95th-percentile duration; and each process's event-loop lag and
  time in garbage collection.
- **RHAPSODY: tasks.** Tasks submitted, completed, failed, canceled and
  running; tasks per second; the mean task duration by backend; and each
  node's CPU, memory and GPU utilization, as RHAPSODY's backends poll them.
- **Application.** An application's own dashboards, from a directory given as
  `APP_DASHBOARDS`, such as a view of the whole application with links to the
  components' dashboards. `HOME_DASHBOARD` makes one of them Grafana's home
  page:

```bash
APP_DASHBOARDS=/path/to/dashboards \
HOME_DASHBOARD=/var/lib/grafana/dashboards/application/overview.json \
    docker compose -f examples/live-dashboard/compose.yaml up -d
```

Prometheus names a metric after its OpenTelemetry name and unit, with dots
turned into underscores, `_seconds` for a duration, and `_total` for a
counter: `flowgentic.agents` is `flowgentic_agents`,
`flowgentic.fleet.call.duration` is `flowgentic_fleet_call_duration_seconds`,
and `flowgentic.fleet.tokens` is `flowgentic_fleet_tokens_total`. Attributes
are labels named the same way, such as `flowgentic_agent_state`.

## What it costs

Streaming costs the agents' process what writing the files costs: the SDK
does the same work per value and per span, and the OTLP exporter encodes
protobuf where the console exporter writes JSON. Measured with the
benchmarks' `--otlp` option into `benchmarks/otlp_sink.py`, an endpoint that
takes the exports and keeps nothing (22 Nov 2026, AMD EPYC 7502P, Python
3.14, the whole process's CPU, median of 5 runs on the loop and 7 for the
host alone):

| SDK, against none | 5,000 agents on the loop (`agent_loop.py`): metrics, a tenth of the agents' spans | The agent host alone, 100,000 agents (`agent_host.py`): metrics |
| --- | --- | --- |
| The files `read_export` reads, metrics every 10 s | +17.4 % | +0.0 % |
| OTLP, metrics every 10 s | +17.7 % | +3.8 % |
| OTLP, metrics every 5 s | +18.4 % | +2.2 % |
| OTLP and the files | +21.0 % | not measured |

The runs vary by about 1 %. The host's run lasts 1.8 s, less than an
interval, so it pays for one export, at the end, which also opens the
connection. Writing the files beside the stream exports each kept span twice:
3 % more than the files alone.

One metrics export, once 5,000 agents on the loop have filled every
instrument, costs 1.5 ms of CPU over OTLP and 2.5 ms as a JSON line; the
first costs about 5 ms. Every 5 s, that is about 1 s of CPU an hour per
process, which is why `configure()` exports twice as often as the telemetry
page's configuration. The spans are what costs: the page's
[levers](telemetry.md#what-it-costs-and-how-to-cut-it) apply as they are,
sampling first.

## Limitations

- `flowgentic.agents` counts the agents of runs in progress only, so "Agents
  done" drops to 0 when a process's run returns; the model calls and tokens
  are totals.
- Prometheus shows a process's series for 5 minutes after its last export.
- RHAPSODY's task duration has the SDK's default buckets, too coarse for the
  quantiles of short tasks, so the dashboard shows its mean.
- With exemplars off, a metric's point does not link to a trace.
- Flowgentic's events are log records, and `configure()` sets no logger
  provider: they are not streamed.

## API

- [`flowgentic.otlp`](../api/flowgentic/otlp.md)
