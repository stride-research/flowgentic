"""One agent process that writes its OpenTelemetry export into a directory, as
the telemetry page configures it, for the tests that read exports back.

    python otel_process.py OUT_DIR AGENTS FAILURES

Its agents run on the loop over a fleet of one scripted replica, whose server
is down for its first ``FAILURES`` calls; ``Recovery`` waits it out. Each
reply counts 100 prompt tokens, 10 completion tokens, 64 cached and 4
reasoning. Prints the fleet's calls, tokens, requests sent again and outages
as JSON.
"""

import asyncio
import json
import sys
from pathlib import Path

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
	ConsoleMetricExporter,
	PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter


def configure(out: Path):
	"""Write metrics every 50 ms and every span into ``out``; return the
	function that flushes and closes them."""
	out.mkdir(parents=True, exist_ok=True)
	metrics_file = open(out / "metrics.jsonl", "w")  # noqa: SIM115
	spans_file = open(out / "spans.jsonl", "w")  # noqa: SIM115
	resource = Resource.create({"service.instance.id": out.name})
	reader = PeriodicExportingMetricReader(
		ConsoleMetricExporter(
			out=metrics_file, formatter=lambda data: data.to_json(indent=None) + "\n"
		),
		export_interval_millis=50,
	)
	meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
	metrics.set_meter_provider(meter_provider)
	tracer_provider = TracerProvider(resource=resource)
	tracer_provider.add_span_processor(
		BatchSpanProcessor(
			ConsoleSpanExporter(
				out=spans_file,
				formatter=lambda span: span.to_json(indent=None) + "\n",
			)
		)
	)
	trace.set_tracer_provider(tracer_provider)

	def close() -> None:
		meter_provider.shutdown()
		tracer_provider.shutdown()
		metrics_file.close()
		spans_file.close()

	return close


async def run(prefix: str, agents: int, failures: int) -> dict:
	from flowgentic.fleet import Fleet, Recovery, Reply, ServerUnavailable, Usage
	from flowgentic.host import AgentHost, Stagger
	from flowgentic.loop import LoopRunner

	class Model:
		model = "scripted"
		base_url = "http://node-1:8000/v1"

		def __init__(self) -> None:
			self.failures = failures

		async def complete(self, messages, tools=None, **kwargs):
			await asyncio.sleep(0.001)
			if self.failures > 0:
				self.failures -= 1
				raise ServerUnavailable("connection refused")
			return Reply(content="DONE", usage=Usage(100, 10, 64, 4), model="scripted")

	model = Model()

	async def check(i):
		return model

	recovery = Recovery(check, poll_s=0.01)
	fleet = Fleet([model], recovery=recovery, name="e2e")
	host = AgentHost(
		LoopRunner(fleet),
		max_live_agents=agents,
		stagger=Stagger(batch=agents, interval_s=0),
		on_done=lambda result, output: fleet.agent_done(result.id),
	)
	report = await host.run_indexed(
		range(agents),
		lambda i: {"messages": [{"role": "user", "content": "go"}]},
		id_prefix=prefix,
	)
	await asyncio.sleep(0.12)  # an export after the run, with every agent gone
	stats = fleet.stats()
	return {
		"outcomes": report.outcomes,
		"calls": stats["calls"],
		"prompt_tokens": stats["prompt_tokens"],
		"completion_tokens": stats["completion_tokens"],
		"retried": stats["retried_calls"],
		"outages": sum(recovery.stats()["outages"].values()),
	}


def main() -> None:
	out, agents, failures = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
	close = configure(out)
	try:
		print(json.dumps(asyncio.run(run(f"{out.name}-", agents, failures))))
	finally:
		close()


if __name__ == "__main__":
	main()
