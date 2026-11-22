# Live dashboard

A run's telemetry, live: an OpenTelemetry collector, Prometheus, Jaeger and
Grafana with Flowgentic's and RHAPSODY's dashboards, started for the user who
runs `stack.sh`, on ports of that user's own.

```bash
examples/live-dashboard/stack.sh up
source <(examples/live-dashboard/stack.sh env)
uv run --extra agents --extra otlp --with "rhapsody-py[telemetry]" \
    python examples/agents-at-scale/campaign.py \
    --agents 12000 --processes 4 --otlp --model-latency 0.5
```

`stack.sh up` prints Grafana's address and where its admin password is.
Each agent process streams to the collector with
`flowgentic.otlp.configure()`, from the `otlp` extra, with the address and
token `stack.sh env` prints. `stack.sh down` stops the stack.

Grafana asks for a login, the collector for a token, and only those two
publish a port, on 127.0.0.1; a port there is still open to every user of
the machine. See the docs before you run it on a shared node.

| File | What it is |
| --- | --- |
| `stack.sh` | Starts and stops this user's stack; makes and keeps its secrets |
| `compose.yaml` | The four services and their ports |
| `collector.yaml` | OTLP in, with a token; metrics to Prometheus, spans to Jaeger; where a model server's metrics would be scraped |
| `prometheus.yaml` | The OTLP receiver's settings: each process's resource as labels |
| `grafana/provisioning/` | The data sources, and the dashboards' folders |
| `dashboards/Flowgentic/`, `dashboards/RHAPSODY/` | One dashboard per component |
| `application/` | Empty: an application's own dashboards come from `APP_DASHBOARDS` |

The docs' [Live dashboard](https://stride-research.github.io/flowgentic/features/live-dashboard/)
page says what `configure()` sets, how to run the stack beside a campaign of
many agent processes and keep it private, what the panels show, and what
streaming costs.
