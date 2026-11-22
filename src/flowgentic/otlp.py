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

import math
import os
import socket
import time
from collections.abc import Sequence
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
	from opentelemetry.sdk.metrics import (
		AlwaysOffExemplarFilter,
		Counter,
		Histogram,
		MeterProvider,
		ObservableCounter,
		ObservableGauge,
		ObservableUpDownCounter,
		UpDownCounter,
	)
	from opentelemetry.sdk.metrics.export import (
		AggregationTemporality,
		ConsoleMetricExporter,
		MetricExporter,
		MetricReader,
		PeriodicExportingMetricReader,
	)
	from opentelemetry.sdk.resources import OTELResourceDetector, Resource
	from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
	from opentelemetry.sdk.trace.export import (
		BatchSpanProcessor,
		ConsoleSpanExporter,
		SpanExporter,
		SpanExportResult,
	)
	from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
except ModuleNotFoundError as exc:
	raise ModuleNotFoundError(
		"flowgentic.otlp needs the otlp extra: pip install 'flowgentic[otlp]'",
		name=exc.name,
	) from exc

try:  # the API's stand-in until the process sets a meter provider
	from opentelemetry.metrics._internal import _ProxyMeterProvider
except ImportError:  # pragma: no cover  # configure() checks after setting
	_ProxyMeterProvider = None

__all__ = ["OtlpTelemetry", "configure"]

SAMPLE = 0.1
"""The share of agents whose spans are kept, each with its whole trace."""
INTERVAL_S = 5.0
"""Seconds between metric exports: a live view refreshes every 5 s, and one
export costs about 1.5 ms of CPU, so about 1 s an hour."""
QUEUE_SIZE = 8_192
"""Spans each OTLP span processor holds for the collector: sixteen batches,
about 30 MB if the collector is down. More are dropped."""
FILES_QUEUE_SIZE = 65_536
"""Spans the ``files`` span processor holds; the SDK's 2,048 drops spans in a
burst."""
TIMEOUT_S = 10.0
"""``timeout_s`` when neither the call nor ``OTEL_EXPORTER_OTLP_TIMEOUT``
gives one, as the exporters' own default."""
_CUMULATIVE = dict.fromkeys(
	(
		Counter,
		UpDownCounter,
		Histogram,
		ObservableCounter,
		ObservableUpDownCounter,
		ObservableGauge,
	),
	AggregationTemporality.CUMULATIVE,
)
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
		self._bound_s = _timeout(timeout_s)
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
					BatchSpanProcessor(
						_spans_file(self._file(files / "spans.jsonl")),
						max_queue_size=FILES_QUEUE_SIZE,
					)
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
		``WorkflowEngine.start_telemetry()``: a metric reader and a span
		processor that export RHAPSODY's metrics and spans to the same
		collector, with this process's resource, export interval and shutdown
		bound.

			await session.start_telemetry(**telemetry.rhapsody_options())

		RHAPSODY keeps providers of its own, which ``configure()`` does not
		reach, and builds them itself: its ``session`` span is always kept;
		each ``task`` span is kept or dropped with the span that submitted the
		task, so with its agent; its metrics keep the SDK's default exemplars,
		which ``OTEL_METRICS_EXEMPLAR_FILTER=always_off`` turns off.

		``start_telemetry()`` also leaves RHAPSODY's ``session`` span current in
		the caller's context until the session closes. Agents started there
		are its children, and per-agent sampling follows it: every agent keeps
		its spans, all in one trace. Start the agents in a context of their
		own:

			token = context.attach(trace.set_span_in_context(trace.INVALID_SPAN))
			try:
				await host.run_indexed(...)
			finally:
				context.detach(token)

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
		"""Export what is left, stop the exports and close the files. With
		the collector down, it takes about ``timeout_s`` for the metrics and
		as much for the spans, and drops the spans still queued. A second
		call does nothing."""
		if self._closed:
			return
		self._closed = True
		try:
			self.meter_provider.shutdown(timeout_millis=1_000 * self._bound_s)
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
			endpoint=self._url("metrics"),
			timeout=self._timeout_s,
			preferred_temporality=_CUMULATIVE,  # whatever the environment prefers
		)

	def _span_exporter(self) -> SpanExporter:
		return OTLPSpanExporter(endpoint=self._url("traces"), timeout=self._timeout_s)

	def _reader(self, exporter: MetricExporter) -> MetricReader:
		return PeriodicExportingMetricReader(
			exporter, export_interval_millis=1_000 * self._interval_s
		)

	def _processor(self, exporter: SpanExporter) -> SpanProcessor:
		return _BoundedSpanProcessor(exporter, self._bound_s)

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
			totals: metrics are cumulative, whatever
			``OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE`` says. It also
			bounds a shutdown with the collector down: about ``timeout_s`` for
			the metrics and as much for the spans, ours or RHAPSODY's.

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
	if _ProxyMeterProvider is not None and not isinstance(
		metrics.get_meter_provider(), _ProxyMeterProvider
	):
		raise RuntimeError(_SET_ALREADY.format("meter"))
	if spans and not isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
		raise RuntimeError(_SET_ALREADY.format("tracer"))
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
		raise RuntimeError(_SET_ALREADY.format("meter"))
	if telemetry.tracer_provider is not None:
		trace.set_tracer_provider(telemetry.tracer_provider)
	return telemetry


_SET_ALREADY = (
	"a {} provider is set already in this process; configure() sets the "
	"process's providers, once"
)


class _Bounded(SpanExporter):
	"""A span exporter that stops exporting at a deadline: the spans of each
	export asked for after it are dropped at once."""

	def __init__(self, exporter: SpanExporter) -> None:
		self._exporter = exporter
		self.deadline = math.inf

	def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
		if time.monotonic() >= self.deadline:
			return SpanExportResult.FAILURE
		return self._exporter.export(spans)

	def shutdown(self) -> None:
		self._exporter.shutdown()

	def force_flush(self, timeout_millis: int = 30_000) -> bool:
		return self._exporter.force_flush(timeout_millis)


class _BoundedSpanProcessor(BatchSpanProcessor):
	"""A ``BatchSpanProcessor`` whose shutdown takes about ``bound_s`` with
	the collector down. The SDK's shutdown exports every queued batch, each
	with its retries, for up to 30 s; this one drops what is left
	``bound_s`` after the shutdown starts, whoever calls it, RHAPSODY's
	session included."""

	def __init__(self, exporter: SpanExporter, bound_s: float) -> None:
		self._bounded = _Bounded(exporter)
		self._bound_s = bound_s
		super().__init__(self._bounded, max_queue_size=QUEUE_SIZE)

	def shutdown(self) -> None:
		self._bounded.deadline = time.monotonic() + self._bound_s
		super().shutdown()


def _timeout(timeout_s: float | None) -> float:
	"""The exporters' timeout: the call's, ``OTEL_EXPORTER_OTLP_TIMEOUT``'s
	(in seconds, as the Python exporters read it), or 10 s."""
	if timeout_s is not None:
		return timeout_s
	try:
		return float(os.environ.get("OTEL_EXPORTER_OTLP_TIMEOUT", TIMEOUT_S))
	except ValueError:
		return TIMEOUT_S


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
		out=out,
		formatter=lambda data: data.to_json(indent=None) + "\n",
		preferred_temporality=_CUMULATIVE,  # read_export refuses delta
	)


def _spans_file(out: IO[str]) -> SpanExporter:
	return ConsoleSpanExporter(
		out=out, formatter=lambda span: span.to_json(indent=None) + "\n"
	)
