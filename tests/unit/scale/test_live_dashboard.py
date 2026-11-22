"""The live dashboard's queries name metrics that exist.

Each Flowgentic and RHAPSODY instrument's name, unit and kind give the name
Prometheus's OTLP receiver stores it under, with its default translation
(``UnderscoreEscapingWithSuffixes``), and every metric a dashboard queries must
be one of them: a renamed instrument fails here, not as an empty panel."""

# ruff: noqa: S101

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("opentelemetry.sdk")
pytest.importorskip("rhapsody")

DASHBOARDS = (
	Path(__file__).resolve().parents[3] / "examples" / "live-dashboard" / "dashboards"
)
FLOWGENTIC = DASHBOARDS / "Flowgentic" / "flowgentic.json"
RHAPSODY = DASHBOARDS / "RHAPSODY" / "rhapsody.json"

# Every instrument each library creates, as (scope, kind, name, unit): the
# SDK's meters are wrapped to record their create_* calls, then Flowgentic's
# telemetry module is imported and a RHAPSODY session starts its telemetry.
INSTRUMENTS = textwrap.dedent(
	"""
	import asyncio, json
	from opentelemetry import metrics
	from opentelemetry.sdk.metrics import MeterProvider

	seen = []

	class Recording:
		def __init__(self, meter, scope):
			self._meter, self._scope = meter, scope

		def __getattr__(self, attr):
			create = getattr(self._meter, attr)
			if not attr.startswith("create_"):
				return create

			def record(name, *args, **kwargs):
				seen.append([self._scope, attr[len("create_"):], name, kwargs.get("unit", "")])
				return create(name, *args, **kwargs)

			return record

	get_meter = MeterProvider.get_meter
	MeterProvider.get_meter = lambda self, name, *a, **k: Recording(
		get_meter(self, name, *a, **k), name
	)
	metrics.set_meter_provider(MeterProvider())
	import flowgentic._telemetry  # noqa: F401  # creates Flowgentic's instruments

	async def rhapsody():
		from concurrent.futures import ThreadPoolExecutor
		from rhapsody import Session
		from rhapsody.backends import ConcurrentExecutionBackend

		session = Session(backends=[await ConcurrentExecutionBackend(ThreadPoolExecutor(1))])
		await session.start_telemetry()
		await session.close()

	asyncio.run(rhapsody())
	print(json.dumps(seen))
	"""
)

UNITS = {"s": "seconds"}  # the units the two libraries use, braces aside
COUNTERS = {"counter", "observable_counter"}
HISTOGRAMS = {"histogram"}


def prometheus_name(kind: str, name: str, unit: str) -> str:
	"""The name Prometheus's OTLP receiver gives an instrument by default:
	dots become underscores, the unit is a suffix unless the name ends with
	it (an annotation in braces is dropped), and a counter ends in _total."""
	out = re.sub(r"[^a-zA-Z0-9_:]", "_", name)
	unit = re.sub(r"\{[^}]*\}", "", unit)
	if unit:
		word = UNITS[unit]  # a new unit: say what Prometheus calls it
		if not out.endswith(f"_{word}"):
			out += f"_{word}"
	if kind in COUNTERS:
		out += "_total"
	return out


@pytest.fixture(scope="module")
def series() -> dict[str, set[str]]:
	"""Each scope's series names in Prometheus, a histogram's three included."""
	done = subprocess.run(
		[sys.executable, "-c", INSTRUMENTS], capture_output=True, text=True, timeout=120
	)
	assert done.returncode == 0, done.stderr
	out: dict[str, set[str]] = {}
	for scope, kind, name, unit in json.loads(done.stdout.strip().splitlines()[-1]):
		base = prometheus_name(kind, name, unit)
		names = (
			{f"{base}_{s}" for s in ("bucket", "sum", "count")}
			if kind in HISTOGRAMS
			else {base}
		)
		out.setdefault(scope, set()).update(names)
	return out


def queried(path: Path) -> set[str]:
	"""Every metric a dashboard's panels and variables select."""
	board = json.loads(path.read_text())
	exprs = [t["expr"] for p in board["panels"] for t in p.get("targets", [])]
	exprs += [
		v["definition"] for v in board["templating"]["list"] if v.get("definition")
	]
	names = set()
	for expr in exprs:
		names.update(re.findall(r"([a-zA-Z_:][a-zA-Z0-9_:]*)\s*[{\[]", expr))
		names.update(re.findall(r"label_values\(\s*([a-zA-Z_:][a-zA-Z0-9_:]*)", expr))
	return names


def test_the_translation_matches_what_prometheus_stored():
	"""Names read back from Prometheus 3.16's OTLP receiver, fed by flowgentic.otlp."""
	assert (
		prometheus_name("observable_gauge", "flowgentic.agents", "{agent}")
		== "flowgentic_agents"
	)
	assert (
		prometheus_name("observable_counter", "flowgentic.agent.time", "s")
		== "flowgentic_agent_time_seconds_total"
	)
	assert (
		prometheus_name("observable_counter", "flowgentic.fleet.tokens", "{token}")
		== "flowgentic_fleet_tokens_total"
	)
	assert (
		prometheus_name("histogram", "flowgentic.fleet.admission.wait", "s")
		== "flowgentic_fleet_admission_wait_seconds"
	)
	assert prometheus_name("counter", "tasks_submitted", "") == "tasks_submitted_total"
	assert prometheus_name("up_down_counter", "tasks_running", "") == "tasks_running"
	assert (
		prometheus_name("histogram", "task_duration_seconds", "")
		== "task_duration_seconds"
	)


def test_every_metric_flowgentic_s_dashboard_queries_is_one_of_flowgentic_s(series):
	names = queried(FLOWGENTIC)
	assert len(names) > 10
	assert names <= series["flowgentic"], names - series["flowgentic"]


def test_every_metric_rhapsody_s_dashboard_queries_is_one_of_rhapsody_s(series):
	names = queried(RHAPSODY)
	assert len(names) > 5
	assert names <= series["rhapsody"], names - series["rhapsody"]


def test_no_query_hides_a_missing_series():
	"""``or vector(0)`` would show 0 for a metric that was renamed away; a
	panel with no series shows its own "0" or "None" instead."""
	for path in (FLOWGENTIC, RHAPSODY):
		assert "vector(0)" not in path.read_text(), path


# --- the stack's defaults ------------------------------------------------------------

STACK = DASHBOARDS.parent


def test_only_grafana_and_the_collector_publish_ports_on_the_bind_address():
	yaml = pytest.importorskip("yaml")
	services = yaml.safe_load((STACK / "compose.yaml").read_text())["services"]
	published = {name: s.get("ports", []) for name, s in services.items()}
	assert {name for name, ports in published.items() if ports} == {
		"grafana",
		"collector",
	}
	for ports in published.values():
		assert all(p.startswith("${BIND_ADDRESS:-127.0.0.1}:${") for p in ports), ports


def test_grafana_needs_a_password_and_the_collector_a_token():
	yaml = pytest.importorskip("yaml")
	services = yaml.safe_load((STACK / "compose.yaml").read_text())["services"]
	grafana = services["grafana"]["environment"]
	assert grafana["GF_SECURITY_ADMIN_PASSWORD"].startswith(
		"${GRAFANA_ADMIN_PASSWORD:?"
	)
	assert grafana["GF_AUTH_ANONYMOUS_ENABLED"] == "${GRAFANA_ANONYMOUS:-false}"
	assert services["collector"]["environment"]["OTLP_TOKEN"].startswith(
		"${OTLP_TOKEN:?"
	)
	collector = yaml.safe_load((STACK / "collector.yaml").read_text())
	protocols = collector["receivers"]["otlp"]["protocols"]
	assert {p["auth"]["authenticator"] for p in protocols.values()} == {
		"bearertokenauth"
	}
	assert "bearertokenauth" in collector["service"]["extensions"]


def test_the_stack_script_parses():
	done = subprocess.run(
		["bash", "-n", str(STACK / "stack.sh")], capture_output=True, text=True
	)
	assert done.returncode == 0, done.stderr
