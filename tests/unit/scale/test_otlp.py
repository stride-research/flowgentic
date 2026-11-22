"""The ``otlp`` extra: an agent process's telemetry streamed over OTLP/HTTP to
a collector, here a receiver in the test that decodes each request's
protobuf, as an OpenTelemetry collector would. No Docker."""

# ruff: noqa: S101

import gzip
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
from collections import Counter, namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("opentelemetry.exporter.otlp.proto.http")

from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (  # noqa: E402
	ExportMetricsServiceRequest,
	ExportMetricsServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (  # noqa: E402
	ExportTraceServiceRequest,
	ExportTraceServiceResponse,
)

from flowgentic.telemetry import read_export  # noqa: E402

PROCESS = Path(__file__).resolve().parent / "otlp_process.py"
EXAMPLE = (
	Path(__file__).resolve().parents[3] / "examples" / "agents-at-scale" / "campaign.py"
)
Span = namedtuple("Span", "scope name trace parent")
CUMULATIVE = 2  # AggregationTemporality.CUMULATIVE in OTLP
FLOWGENTIC_METRICS = {
	"flowgentic.agents",
	"flowgentic.agent.time",
	"flowgentic.fleet.admission.wait",
	"flowgentic.fleet.request.duration",
	"flowgentic.fleet.call.duration",
	"flowgentic.fleet.retries",
	"flowgentic.fleet.outage.duration",
	"flowgentic.fleet.tokens",
}


# --- a collector's OTLP/HTTP receiver, in the test --------------------------------


class Receiver:
	"""An OTLP/HTTP receiver on 127.0.0.1: it decodes each export request's
	protobuf, keeps it, and answers as a collector does."""

	def __init__(self) -> None:
		self.metrics: list[ExportMetricsServiceRequest] = []
		self.traces: list[ExportTraceServiceRequest] = []
		self.content_types: set[str] = set()
		receiver = self

		class Handler(BaseHTTPRequestHandler):
			def do_POST(self) -> None:
				body = self.rfile.read(int(self.headers["Content-Length"]))
				if self.headers.get("Content-Encoding") == "gzip":
					body = gzip.decompress(body)
				receiver.content_types.add(self.headers.get("Content-Type", ""))
				if self.path == "/v1/metrics":
					receiver.metrics.append(
						ExportMetricsServiceRequest.FromString(body)
					)
					reply = ExportMetricsServiceResponse().SerializeToString()
				elif self.path == "/v1/traces":
					receiver.traces.append(ExportTraceServiceRequest.FromString(body))
					reply = ExportTraceServiceResponse().SerializeToString()
				else:
					self.send_error(404)
					return
				self.send_response(200)
				self.send_header("Content-Type", "application/x-protobuf")
				self.send_header("Content-Length", str(len(reply)))
				self.end_headers()
				self.wfile.write(reply)

			def log_message(self, *args: object) -> None:
				pass

		self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
		self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
		self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

	def __enter__(self) -> "Receiver":
		self._thread.start()
		return self

	def __exit__(self, *exc: object) -> None:
		self._server.shutdown()
		self._server.server_close()

	def resources(self) -> list[dict]:
		"""The attributes of every resource that sent metrics or spans."""
		out = []
		for request in self.metrics:
			out += [
				_attributes(r.resource.attributes) for r in request.resource_metrics
			]
		for request in self.traces:
			out += [_attributes(r.resource.attributes) for r in request.resource_spans]
		return out

	def metric_names(self, scope: str) -> set[str]:
		return {m.name for m in self._metrics(scope)}

	def last(self, name: str, scope: str = "flowgentic") -> list:
		"""The data points of metric ``name`` in the last request that held
		it: with cumulative temporality, the totals."""
		found = [m for m in self._metrics(scope) if m.name == name]
		assert found, f"no {name}"
		metric = found[-1]
		kind = metric.WhichOneof("data")
		return list(getattr(metric, kind).data_points)

	def points(self, name: str, scope: str = "flowgentic") -> list:
		"""The data points of metric ``name`` in every request."""
		return [
			p
			for m in self._metrics(scope)
			if m.name == name
			for p in getattr(m, m.WhichOneof("data")).data_points
		]

	def _metrics(self, scope: str) -> list:
		return [
			m
			for request in self.metrics
			for r in request.resource_metrics
			for s in r.scope_metrics
			if s.scope.name == scope
			for m in s.metrics
		]

	def span_records(self) -> list[Span]:
		"""Every span received: its scope, name, trace and parent."""
		return [
			Span(
				s.scope.name,
				span.name.split()[0],
				span.trace_id.hex(),
				span.parent_span_id.hex(),
			)
			for request in self.traces
			for r in request.resource_spans
			for s in r.scope_spans
			for span in s.spans
		]

	def spans(self) -> list[tuple[str, dict]]:
		"""Every span received: its name and attributes."""
		return [
			(span.name, _attributes(span.attributes))
			for request in self.traces
			for r in request.resource_spans
			for s in r.scope_spans
			for span in s.spans
		]


def _attributes(pairs) -> dict:
	out = {}
	for kv in pairs:
		kind = kv.value.WhichOneof("value")
		out[kv.key] = getattr(kv.value, kind) if kind else None
	return out


def _by(points: list, key: str) -> dict:
	return {_attributes(p.attributes).get(key): p for p in points}


def run_process(*args: str, env: dict | None = None, timeout: float = 120) -> dict:
	done = subprocess.run(
		[sys.executable, str(PROCESS), *args],
		capture_output=True,
		text=True,
		check=False,
		timeout=timeout,
		env={**os.environ, **(env or {})},
	)
	assert done.returncode == 0, done.stderr
	return json.loads(done.stdout.strip().splitlines()[-1])


# --- the tests -------------------------------------------------------------------


def test_an_agent_process_streams_its_metrics_and_spans_to_a_collector():
	with Receiver() as collector:
		run = run_process(
			"--endpoint", collector.url,
			"--agents", "60", "--failures", "2", "--sample", "1",
			"--run", "demo", "--service", "agents", "--instance", "p000",
		)  # fmt: skip
	assert run["outcomes"] == {"done": 60}
	assert collector.metrics and collector.traces
	assert collector.content_types == {"application/x-protobuf"}

	# Every request names the run, the process's role and the process.
	for resource in collector.resources():
		assert resource["service.namespace"] == "demo"
		assert resource["service.name"] == "agents"
		assert resource["service.instance.id"] == "p000"

	assert FLOWGENTIC_METRICS <= collector.metric_names("flowgentic")
	(calls,) = collector.last("flowgentic.fleet.call.duration")
	assert calls.count == 60
	tokens = _by(collector.last("flowgentic.fleet.tokens"), "gen_ai.token.type")
	assert (tokens["input"].as_int, tokens["output"].as_int) == (6_000, 600)
	assert sum(p.as_int for p in collector.last("flowgentic.fleet.retries")) == 2
	assert {p.as_int for p in collector.last("flowgentic.agents")} == {0}  # all done
	for request in collector.metrics:
		for r in request.resource_metrics:
			for s in r.scope_metrics:
				for m in s.metrics:
					kind = m.WhichOneof("data")
					if kind in ("sum", "histogram"):
						assert getattr(m, kind).aggregation_temporality == CUMULATIVE

	names = Counter(name.split()[0] for name, _ in collector.spans())
	assert names["invoke_agent"] == 60 and names["chat"] == 60
	chats = [attrs for name, attrs in collector.spans() if name.startswith("chat")]
	assert all(a["gen_ai.usage.input_tokens"] == 100 for a in chats)

	# Exemplars are off, though every value was recorded inside a sampled span.
	assert not any(
		p.exemplars for p in collector.points("flowgentic.fleet.call.duration")
	)


def test_without_names_the_process_is_its_host_and_pid_and_the_environment_wins():
	with Receiver() as collector:
		run_process("--endpoint", collector.url, "--agents", "5")
	(resource,) = {tuple(sorted(r.items())) for r in collector.resources()}
	resource = dict(resource)
	assert resource["service.name"] == "flowgentic"
	host, _, pid = resource["service.instance.id"].rpartition("-")
	assert host == socket.gethostname() and pid.isdigit()  # not the SDK's random id
	assert "service.namespace" not in resource

	env = {
		"OTEL_RESOURCE_ATTRIBUTES": "service.instance.id=rank-7,service.namespace=c1",
		"OTEL_SERVICE_NAME": "workers",
	}
	with Receiver() as collector:
		run_process("--endpoint", collector.url, "--agents", "5", env=env)
	for resource in collector.resources():
		assert resource["service.instance.id"] == "rank-7"
		assert resource["service.namespace"] == "c1"
		assert resource["service.name"] == "workers"


def test_by_default_a_tenth_of_the_agents_keep_their_spans_and_every_call_is_counted():
	with Receiver() as collector:
		run = run_process("--endpoint", collector.url, "--agents", "200")
	assert run["outcomes"] == {"done": 200}
	(calls,) = collector.last("flowgentic.fleet.call.duration")
	assert calls.count == 200
	names = Counter(name.split()[0] for name, _ in collector.spans())
	assert 0 < names["invoke_agent"] < 100  # 20 expected; 0 or 100 has p < 1e-9
	assert names["chat"] == names["invoke_agent"]  # a kept agent keeps its trace


def test_exemplars_can_be_turned_on():
	with Receiver() as collector:
		run_process(
			"--endpoint", collector.url, "--agents", "20", "--sample", "1", "--exemplars"
		)  # fmt: skip
	# An export's exemplars are those of the values since the one before.
	assert any(p.exemplars for p in collector.points("flowgentic.fleet.call.duration"))


def test_metrics_only():
	with Receiver() as collector:
		run_process("--endpoint", collector.url, "--agents", "20", "--no-spans")
	assert collector.metrics and not collector.traces
	(calls,) = collector.last("flowgentic.fleet.call.duration")
	assert calls.count == 20


def test_the_files_that_read_export_reads_are_written_beside_the_stream(tmp_path):
	with Receiver() as collector:
		run_process(
			"--agents", "40", "--failures", "1", "--sample", "1",
			"--files", str(tmp_path / "p000"),
			env={"OTEL_EXPORTER_OTLP_ENDPOINT": collector.url},
		)  # fmt: skip
	(calls,) = collector.last("flowgentic.fleet.call.duration")
	assert calls.count == 40  # the endpoint came from the environment
	export = read_export(tmp_path)
	assert export.check() == []
	assert export.calls == 40 and export.chat_spans == 40 and export.retries == 1


def test_rhapsody_s_telemetry_goes_to_the_same_collector_as_the_process():
	with Receiver() as collector:
		run = run_process(
			"--endpoint", collector.url, "--agents", "0", "--rhapsody", "3",
			"--instance", "p007",
		)  # fmt: skip
	assert run["rhapsody_tasks"] == 3
	assert {"tasks_submitted", "tasks_completed"} <= collector.metric_names("rhapsody")
	(completed,) = collector.last("tasks_completed", scope="rhapsody")
	assert completed.as_int == 3
	assert {r["service.instance.id"] for r in collector.resources()} == {"p007"}


def test_a_collector_that_is_down_does_not_stop_the_agents():
	with socket.socket() as s:
		s.bind(("127.0.0.1", 0))
		port = s.getsockname()[1]  # nothing listens once it is closed
	run = run_process(
		"--endpoint", f"http://127.0.0.1:{port}", "--agents", "30", "--timeout", "1",
		timeout=60,
	)  # fmt: skip
	assert run["outcomes"] == {"done": 30}


def test_metrics_stay_cumulative_whatever_the_environment_prefers(tmp_path):
	with Receiver() as collector:
		run_process(
			"--endpoint", collector.url, "--agents", "20", "--files", str(tmp_path / "p000"),
			env={"OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "DELTA"},
		)  # fmt: skip
	temporalities = {
		getattr(m, m.WhichOneof("data")).aggregation_temporality
		for request in collector.metrics
		for r in request.resource_metrics
		for s in r.scope_metrics
		for m in s.metrics
		if m.WhichOneof("data") in ("sum", "histogram")
	}
	assert temporalities == {CUMULATIVE}  # Prometheus's panels need the totals
	(calls,) = collector.last("flowgentic.fleet.call.duration")
	assert calls.count == 20
	assert read_export(tmp_path).check() == []  # read_export refuses delta


def test_the_example_keeps_a_tenth_of_its_agents_each_the_root_of_its_trace():
	"""RHAPSODY's ``start_telemetry()`` leaves its session span current; the
	example's agents must still start traces of their own, or every agent
	follows the session's sampling and lands in one trace."""
	with Receiver() as collector:
		done = subprocess.run(
			[sys.executable, str(EXAMPLE), "--agents", "300", "--otlp", collector.url],
			capture_output=True,
			text=True,
			check=False,
			timeout=240,
		)
	assert done.returncode == 0, done.stderr
	spans = collector.span_records()
	agents = [s for s in spans if s.name == "invoke_agent"]
	assert 0 < len(agents) < 150  # 30 expected; 0 or 150 has p < 1e-12
	assert not any(s.parent for s in agents)  # each the root of its trace
	kept = {s.trace for s in agents}
	assert len(kept) == len(agents)
	tasks = [s for s in spans if (s.scope, s.name) == ("rhapsody", "task")]
	assert len(tasks) == len(agents)  # one simulation per agent, kept with it
	assert {s.trace for s in tasks} <= kept
	(session,) = [s for s in spans if (s.scope, s.name) == ("rhapsody", "session")]
	assert session.trace not in kept


def test_shutdown_with_the_collector_down_is_bounded():
	"""Spans queued for a collector that is down: shutting down ours, and
	RHAPSODY's with the processor ``rhapsody_options()`` gave it, each takes
	about ``timeout_s``, not a retry per queued batch up to the batch
	processor's 30 s (26 s for 20,000 spans at ``timeout_s=1`` before)."""
	code = textwrap.dedent(
		"""
		import socket, time
		from opentelemetry import trace
		from opentelemetry.sdk.trace import TracerProvider
		from flowgentic.otlp import configure

		def spans(tracer, name):
			for _ in range(20_000):
				with tracer.start_as_current_span(name):
					pass

		with socket.socket() as s:
			s.bind(("127.0.0.1", 0))
			port = s.getsockname()[1]  # nothing listens once it is closed
		telemetry = configure(f"http://127.0.0.1:{port}", sample=1.0, timeout_s=1)
		options = telemetry.rhapsody_options()
		spans(trace.get_tracer("test"), "invoke_agent")
		t = time.monotonic()
		telemetry.shutdown()
		ours_s = time.monotonic() - t
		theirs = TracerProvider(resource=options["resource"])  # as RHAPSODY builds it
		for processor in options["span_processors"]:
			theirs.add_span_processor(processor)
		spans(theirs.get_tracer("rhapsody"), "task")
		t = time.monotonic()
		theirs.shutdown()  # as RHAPSODY's session.close() does
		print(ours_s, time.monotonic() - t)
		"""
	)
	done = subprocess.run(
		[sys.executable, "-c", code], capture_output=True, text=True, timeout=300
	)
	assert done.returncode == 0, done.stderr
	ours_s, rhapsody_s = map(float, done.stdout.split())
	assert ours_s < 4  # spans and metrics: a second each, and an export under way
	assert rhapsody_s < 4


def test_a_refused_process_keeps_its_files_and_sends_nothing(tmp_path):
	out = tmp_path / "p000"
	out.mkdir()
	(out / "metrics.jsonl").write_text("an earlier run\n")
	code = (
		"import sys\n"
		"from opentelemetry import metrics\n"
		"from opentelemetry.sdk.metrics import MeterProvider\n"
		"metrics.set_meter_provider(MeterProvider())\n"
		"from flowgentic.otlp import configure\n"
		"configure(sys.argv[1], files=sys.argv[2], interval_s=0.05)\n"
	)
	with Receiver() as collector:
		done = subprocess.run(
			[sys.executable, "-c", code, collector.url, str(out)],
			capture_output=True,
			text=True,
			timeout=60,
		)
	assert "RuntimeError: a meter provider is set already" in done.stderr
	assert (out / "metrics.jsonl").read_text() == "an earlier run\n"
	assert not (out / "spans.jsonl").exists()
	assert not collector.metrics and not collector.traces


def test_a_process_whose_providers_are_set_already_is_refused():
	code = (
		"from opentelemetry import metrics\n"
		"from opentelemetry.sdk.metrics import MeterProvider\n"
		"metrics.set_meter_provider(MeterProvider())\n"
		"from flowgentic.otlp import configure\n"
		"configure('http://127.0.0.1:9')\n"
	)
	done = subprocess.run(
		[sys.executable, "-c", code], capture_output=True, text=True, timeout=60
	)
	assert done.returncode != 0
	assert "RuntimeError: a meter provider is set already" in done.stderr


def test_without_the_extra_the_error_names_it(monkeypatch):
	monkeypatch.delitem(sys.modules, "flowgentic.otlp", raising=False)
	exporters = "opentelemetry.exporter.otlp.proto.http"
	for name in (
		exporters,
		f"{exporters}.metric_exporter",
		f"{exporters}.trace_exporter",
	):
		monkeypatch.setitem(sys.modules, name, None)  # as if it were not installed
	with pytest.raises(ModuleNotFoundError, match=r"flowgentic\[otlp\]"):
		import flowgentic.otlp  # noqa: F401
