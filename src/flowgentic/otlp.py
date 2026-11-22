"""Stream a process's telemetry to an OpenTelemetry collector over OTLP, for a
live dashboard. It needs the ``otlp`` extra.

Flowgentic depends on ``opentelemetry-api`` only, and the application
configures the SDK. ``configure()`` does it for one process, in one call: it
sets the process's meter and tracer providers to export over OTLP/HTTP to a
collector, with the telemetry page's configuration at scale, metrics every
5 s. Everything in
the process that records through OpenTelemetry's global providers goes with
them: Flowgentic's metrics and spans, and the MCP SDK's and the GenAI
instrumentation's spans. RHAPSODY keeps providers of its own;
``OtlpTelemetry.rhapsody_options()`` gives them a reader and a span processor
that export to the same collector, with the same resource.

    from flowgentic.otlp import configure

    telemetry = configure("http://localhost:4318", run="campaign-7", instance="p003")
    ...  # run the agent hosts
    telemetry.shutdown()

The resource names the run (``service.namespace``), the process's role in it
(``service.name``) and the process (``service.instance.id``), so a dashboard
can show a campaign of many agent processes, or one of them.
``examples/live-dashboard`` starts a collector, Prometheus, Jaeger and Grafana,
with Flowgentic's and RHAPSODY's dashboards, in one command.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from types import TracebackType
from typing import IO, Any

from opentelemetry import metrics, trace

try:
	from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
		OTLPMetricExporter,
	)
	from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
		OTLPSpanExporter,
	)
	from opentelemetry.sdk.metrics import AlwaysOffExemplarFilter, MeterProvider
	from opentelemetry.sdk.metrics.export import (
		ConsoleMetricExporter,
		MetricExporter,
		MetricReader,
		PeriodicExportingMetricReader,
	)
	from opentelemetry.sdk.resources import OTELResourceDetector, Resource
	from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
	from opentelemetry.sdk.trace.export import (
		BatchSpanProcessor,
		ConsoleSpanExporter,
		SpanExporter,
	)
	from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
except ModuleNotFoundError as exc:
	raise ModuleNotFoundError(
		"flowgentic.otlp needs the otlp extra: pip install 'flowgentic[otlp]'",
		name=exc.name,
	) from exc

__all__ = ["OtlpTelemetry", "configure"]

SAMPLE = 0.1
"""The share of agents whose spans are kept, each with its whole trace."""
INTERVAL_S = 5.0
"""Seconds between metric exports: a live view refreshes every 5 s, and one
export costs about 1.5 ms of CPU, so about 1 s an hour."""
MAX_QUEUE_SIZE = 65_536
"""Spans a ``BatchSpanProcessor`` holds; the SDK's 2,048 drops spans in a burst."""
SERVICE = "flowgentic"
"""``service.name`` when neither the call nor the environment gives one."""


class OtlpTelemetry:
	"""One process's OpenTelemetry SDK, exporting to a collector over
	OTLP/HTTP; ``configure()`` makes it and sets its providers as the
	process's.

	``shutdown()`` exports what is left and stops the exports; it is also
	what leaving a ``with`` block does."""

	def __init__(
		self,
		resource: Resource,
		endpoint: str | None,
		sample: float,
		exemplars: bool,
		interval_s: float,
		spans: bool,
		files: Path | None,
		timeout_s: float | None,
	) -> None:
		self.resource = resource
		"""The process's resource: the run, the process's role, the process."""
		self.endpoint = endpoint
		"""The collector's OTLP/HTTP base URL, or ``None`` for the
		environment's (``OTEL_EXPORTER_OTLP_ENDPOINT``, by default
		``http://localhost:4318``)."""
		self.files = files
		"""The directory that also holds ``metrics.jsonl`` and ``spans.jsonl``,
		as ``flowgentic.telemetry.read_export`` reads them, or ``None``."""
		self._interval_s = interval_s
		self._timeout_s = timeout_s
		self._open: list[IO[str]] = []
		self._closed = False
		readers: list[MetricReader] = [self._reader(self._metric_exporter())]
		processors: list[SpanProcessor] = (
			[self._processor(self._span_exporter())] if spans else []
		)
		if files is not None:
			files.mkdir(parents=True, exist_ok=True)
			readers.append(
				self._reader(_metrics_file(self._file(files / "metrics.jsonl")))
			)
			if spans:
				processors.append(
					self._processor(_spans_file(self._file(files / "spans.jsonl")))
				)
		self.meter_provider = MeterProvider(
			resource=resource,
			metric_readers=readers,
			exemplar_filter=None if exemplars else AlwaysOffExemplarFilter(),
		)
		"""The process's meter provider."""
		self.tracer_provider: TracerProvider | None = None
		"""The process's tracer provider; ``None`` when spans are not exported."""
		if spans:
			self.tracer_provider = TracerProvider(
				resource=resource, sampler=ParentBased(TraceIdRatioBased(sample))
			)
			for processor in processors:
				self.tracer_provider.add_span_processor(processor)

	def rhapsody_options(self) -> dict[str, Any]:
		"""Options for RHAPSODY's ``Session.start_telemetry()``, or AsyncFlow's
		``WorkflowEngine.start_telemetry()``, that export RHAPSODY's metrics and
		spans to the same collector, with this process's resource:

			await session.start_telemetry(**telemetry.rhapsody_options())

		RHAPSODY keeps its own providers, which ``configure()`` does not reach.
		Each call makes a new reader and span processor; RHAPSODY shuts them
		down when its session closes."""
		options: dict[str, Any] = {
			"metric_readers": [self._reader(self._metric_exporter())],
			"resource": self.resource,
		}
		if self.tracer_provider is not None:
			options["span_processors"] = [self._processor(self._span_exporter())]
		return options

	def shutdown(self) -> None:
		"""Export what is left, stop the exports and close the files. A
		collector that is down costs at most the exporters' timeout per
		signal. A second call does nothing."""
		if self._closed:
			return
		self._closed = True
		try:
			self.meter_provider.shutdown()
			if self.tracer_provider is not None:
				self.tracer_provider.shutdown()
		finally:
			for f in self._open:
				f.close()

	def __enter__(self) -> OtlpTelemetry:
		return self

	def __exit__(
		self,
		exc_type: type[BaseException] | None,
		exc: BaseException | None,
		tb: TracebackType | None,
	) -> None:
		self.shutdown()

	def _url(self, signal: str) -> str | None:
		if self.endpoint is None:
			return None  # the exporter reads the environment
		return f"{self.endpoint.rstrip('/')}/v1/{signal}"

	def _metric_exporter(self) -> MetricExporter:
		return OTLPMetricExporter(
			endpoint=self._url("metrics"), timeout=self._timeout_s
		)

	def _span_exporter(self) -> SpanExporter:
		return OTLPSpanExporter(endpoint=self._url("traces"), timeout=self._timeout_s)

	def _reader(self, exporter: MetricExporter) -> MetricReader:
		return PeriodicExportingMetricReader(
			exporter, export_interval_millis=1_000 * self._interval_s
		)

	@staticmethod
	def _processor(exporter: SpanExporter) -> SpanProcessor:
		return BatchSpanProcessor(exporter, max_queue_size=MAX_QUEUE_SIZE)

	def _file(self, path: Path) -> IO[str]:
		f = path.open("w")
		self._open.append(f)
		return f


def configure(
	endpoint: str | None = None,
	*,
	run: str | None = None,
	service: str | None = None,
	instance: str | None = None,
	sample: float = SAMPLE,
	exemplars: bool = False,
	interval_s: float = INTERVAL_S,
	spans: bool = True,
	files: str | os.PathLike[str] | None = None,
	timeout_s: float | None = None,
) -> OtlpTelemetry:
	"""Set this process's meter and tracer providers to export to an
	OpenTelemetry collector over OTLP/HTTP; return them, to shut them down
	once the run ends.

	Call it once per process, before the agent hosts run. It refuses a
	process whose providers are set already: the SDK keeps the first ones,
	and Flowgentic's telemetry would not reach the collector.

	The defaults are the telemetry page's configuration at scale: spans
	sampled per agent, each agent's ``invoke_agent`` span the root of its
	trace, so ``ParentBased(TraceIdRatioBased(sample))`` keeps a ``sample``
	share of the agents, each with its whole trace; no exemplars. Metrics go
	every 5 s, not 10 s, for a live view: one export costs about 1.5 ms of
	CPU. The metrics count every agent and call, sampled spans or not.

	Args:
		endpoint: The collector's OTLP/HTTP base URL, such as
			``http://localhost:4318``; the exporters add ``/v1/metrics`` and
			``/v1/traces``. ``None`` reads OpenTelemetry's environment
			variables, ``OTEL_EXPORTER_OTLP_ENDPOINT`` and the per-signal
			ones, as the exporters do; without them, ``http://localhost:4318``.
		run: The run, or campaign, the process belongs to: the resource's
			``service.namespace``.
		service: The process's role in the run, such as ``agents``: its
			``service.name``. Without it, ``OTEL_SERVICE_NAME`` or
			``OTEL_RESOURCE_ATTRIBUTES``, then ``flowgentic``.
		instance: The process: its ``service.instance.id``, one per process.
			Without it, ``OTEL_RESOURCE_ATTRIBUTES``, then the host's name and
			the process id.
		sample: The share of agents whose spans are kept, from 0 to 1.
		exemplars: Keep the SDK's default exemplar filter, which keeps an
			exemplar for each value recorded inside a sampled span.
		interval_s: Seconds between metric exports.
		spans: Export spans; ``False`` exports metrics only.
		files: Also write ``metrics.jsonl`` and ``spans.jsonl`` into this
			directory, as ``flowgentic.telemetry.read_export`` reads them, with
			the same configuration.
		timeout_s: Seconds an export waits for the collector, retries
			included; ``None`` reads ``OTEL_EXPORTER_OTLP_TIMEOUT``, by default
			10. An export that fails is dropped and the next one carries the
			totals: metrics are cumulative.

	Raises:
		ValueError: ``sample`` is not between 0 and 1, or ``interval_s`` is not
			positive.
		RuntimeError: The process has a meter provider, or with ``spans`` a
			tracer provider, set already.
	"""
	if not 0 <= sample <= 1:
		raise ValueError(f"sample must be between 0 and 1, not {sample}")
	if interval_s <= 0:
		raise ValueError(f"interval_s must be positive, not {interval_s}")
	if spans and not isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
		raise RuntimeError(
			"a tracer provider is set already in this process; configure() sets "
			"the process's providers, once"
		)
	telemetry = OtlpTelemetry(
		_resource(run, service, instance),
		endpoint,
		sample,
		exemplars,
		interval_s,
		spans,
		None if files is None else Path(files),
		timeout_s,
	)
	metrics.set_meter_provider(telemetry.meter_provider)
	if metrics.get_meter_provider() is not telemetry.meter_provider:
		telemetry.shutdown()
		raise RuntimeError(
			"a meter provider is set already in this process; configure() sets "
			"the process's providers, once"
		)
	if telemetry.tracer_provider is not None:
		trace.set_tracer_provider(telemetry.tracer_provider)
	return telemetry


def _resource(run: str | None, service: str | None, instance: str | None) -> Resource:
	"""The call's attributes, over the environment's, over the defaults. The
	SDK's own default for ``service.instance.id`` is a random id, which a
	dashboard cannot name a process by: the host and the process id replace
	it."""
	env = OTELResourceDetector().detect().attributes  # OTEL_RESOURCE_ATTRIBUTES, ...
	attributes: dict[str, str] = {}
	if "service.name" not in env:
		attributes["service.name"] = SERVICE
	if "service.instance.id" not in env:
		attributes["service.instance.id"] = f"{socket.gethostname()}-{os.getpid()}"
	given = {
		"service.namespace": run,
		"service.name": service,
		"service.instance.id": instance,
	}
	attributes.update({k: v for k, v in given.items() if v is not None})
	return Resource.create(attributes)


def _metrics_file(out: IO[str]) -> MetricExporter:
	return ConsoleMetricExporter(
		out=out, formatter=lambda data: data.to_json(indent=None) + "\n"
	)


def _spans_file(out: IO[str]) -> SpanExporter:
	return ConsoleSpanExporter(
		out=out, formatter=lambda span: span.to_json(indent=None) + "\n"
	)
