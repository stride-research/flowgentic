# Live dashboard

A run's telemetry, live: an OpenTelemetry collector, Prometheus, Jaeger and
Grafana with Flowgentic's and RHAPSODY's dashboards, in one command. Every
port is bound to 127.0.0.1.

```bash
docker compose -f examples/live-dashboard/compose.yaml up -d
uv run --extra agents --extra otlp python examples/agents-at-scale/campaign.py \
    --agents 12000 --processes 4 --otlp --model-latency 0.5
```

Then open <http://localhost:3000>. Each agent process streams to the
collector with `flowgentic.otlp.configure()`, from the `otlp` extra.

| File | What it is |
| --- | --- |
| `compose.yaml` | The four services and their ports |
| `collector.yaml` | OTLP in; metrics to Prometheus, spans to Jaeger; where a model server's metrics would be scraped |
| `prometheus.yaml` | The OTLP receiver's settings: each process's resource as labels |
| `grafana/provisioning/` | The data sources, and the dashboards' folders |
| `dashboards/Flowgentic/`, `dashboards/RHAPSODY/` | One dashboard per component |
| `application/` | Empty: an application's own dashboards come from `APP_DASHBOARDS` |

The docs' [Live dashboard](https://stride-research.github.io/flowgentic/features/live-dashboard/)
page says what `configure()` sets, how to run the dashboard beside a campaign
of many agent processes, what the panels show, and what streaming costs.
