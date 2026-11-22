"""Reading Flowgentic's OpenTelemetry export back: one per process, merged, and
checked against itself and against the model servers' own counters."""

# ruff: noqa: S101

import json
import subprocess
import sys
from pathlib import Path

import pytest

from flowgentic.fleet import Usage
from flowgentic.telemetry import Export, read_export

NS = 10**9
PROCESS = Path(__file__).resolve().parent / "otel_process.py"
FLEET = {"flowgentic.fleet.name": "q", "flowgentic.fleet.replica": 0}


# --- an export as the SDK's console exporters write it ------------------------------


def export(*metrics, scope="flowgentic"):
	"""One line of ``metrics.jsonl``: ``MetricsData.to_json()``."""
	return {
		"resource_metrics": [
			{
				"resource": {"attributes": {"service.name": "test"}, "schema_url": ""},
				"scope_metrics": [
					{
						"scope": {"name": scope, "version": "0.6.0"},
						"metrics": list(metrics),
						"schema_url": "",
					}
				],
				"schema_url": "",
			}
		]
	}


def gauge(name, t, *values):
	"""``values``: (attributes, value) pairs."""
	points = [
		{"attributes": a, "start_time_unix_nano": None, "time_unix_nano": int(t * NS), "value": v}
		for a, v in values
	]
	return {"name": name, "unit": "", "data": {"data_points": points}}


def counter(name, t, *values, start=1.0, temporality=2):
	points = [
		{
			"attributes": a,
			"start_time_unix_nano": int(start * NS),
			"time_unix_nano": int(t * NS),
			"value": v,
		}
		for a, v in values
	]
	data = {"data_points": points, "aggregation_temporality": temporality, "is_monotonic": True}
	return {"name": name, "unit": "", "data": data}


def histogram(name, t, *values, start=1.0, temporality=2):
	"""``values``: (attributes, count, sum) triples."""
	points = [
		{
			"attributes": a,
			"start_time_unix_nano": int(start * NS),
			"time_unix_nano": int(t * NS),
			"count": c,
			"sum": s,
			"bucket_counts": [c],
			"explicit_bounds": [],
		}
		for a, c, s in values
	]
	data = {"data_points": points, "aggregation_temporality": temporality}
	return {"name": name, "unit": "s", "data": data}


def chat(ok=True, fleet=True, usage=(100, 10, None, None)):
	"""One line of ``spans.jsonl``: a ``chat`` span, ``ReadableSpan.to_json()``."""
	attributes = {"gen_ai.operation.name": "chat", "gen_ai.request.model": "m"}
	if fleet:
		attributes.update(FLEET)
	if ok:
		prompt, completion, cached, reasoning = usage
		attributes["gen_ai.usage.input_tokens"] = prompt
		attributes["gen_ai.usage.output_tokens"] = completion
		if cached:
			attributes["gen_ai.usage.cache_read.input_tokens"] = cached
		if reasoning:
			attributes["gen_ai.usage.reasoning.output_tokens"] = reasoning
	else:
		attributes["error.type"] = "ServerUnavailable"
	return {
		"name": "chat m",
		"context": {"trace_id": "0x1", "span_id": "0x2", "trace_state": "[]"},
		"kind": "SpanKind.CLIENT",
		"parent_id": None,
		"start_time": "2026-11-22T00:00:00.000000Z",
		"end_time": "2026-11-22T00:00:01.000000Z",
		"status": {"status_code": "UNSET" if ok else "ERROR"},
		"attributes": attributes,
		"events": [],
		"links": [],
		"resource": {"attributes": {}, "schema_url": ""},
	}


def write(path: Path, *lines) -> Path:
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text("".join(json.dumps(line) + "\n" for line in lines))
	return path


def calls(t, ok, failed=0, start=1.0):
	values = [(FLEET, ok, 0.5 * ok)]
	if failed:
		values.append(({**FLEET, "error.type": "ServerUnavailable"}, failed, 1.0))
	return histogram("flowgentic.fleet.call.duration", t, *values, start=start)


def agents(t, **by_state):
	return gauge(
		"flowgentic.agents", t, *(({"flowgentic.agent.state": s}, n) for s, n in by_state.items())
	)


# --- merging ------------------------------------------------------------------------


def test_the_processes_exports_merge_into_one(tmp_path):
	write(
		tmp_path / "p0" / "metrics.jsonl",
		export(agents(10, working=2, done=0), calls(10, 1)),
		export(agents(12, working=0, done=2), calls(12, 4)),
	)
	write(
		tmp_path / "p1" / "metrics.jsonl",
		export(agents(11, working=3, done=0), calls(11, 2)),
		export(
			agents(13, working=1, done=2),
			calls(13, 5, failed=1),
			counter("flowgentic.fleet.retries", 13, ({**FLEET, "error.type": "ServerUnavailable"}, 3)),
			histogram(
				"flowgentic.fleet.outage.duration",
				13,
				({**FLEET, "flowgentic.fleet.outage.outcome": "recovered"}, 2, 7.5),
				({**FLEET, "flowgentic.fleet.outage.outcome": "gave_up"}, 1, 30.0),
			),
			histogram("flowgentic.fleet.request.duration", 13, (FLEET, 8, 4.0)),
			counter(
				"flowgentic.agent.time",
				13,
				({"flowgentic.agent.state": "model_request", "flowgentic.agent.category": "model_request"}, 4.0),
				({"flowgentic.agent.state": "working", "flowgentic.agent.category": "working"}, 1.5),
			),
		),
		export(gauge("other.metric", 14, ({}, 1)), scope="someone.else"),
	)
	got = read_export(tmp_path)
	assert isinstance(got, Export)
	assert got.processes == 2 and got.exports == 4
	# At each export's time, each process's latest values, summed
	assert got.agents == (
		(10.0, {"working": 2, "done": 0}),
		(11.0, {"working": 5, "done": 0}),
		(12.0, {"working": 3, "done": 2}),
		(13.0, {"working": 1, "done": 4}),
	)
	assert got.calls == 4 + 6 and got.failed_calls == 1
	assert got.requests == 8 and got.retries == 3
	assert (got.outages, got.gave_up, got.down_s) == (2, 1, 37.5)
	assert got.agent_seconds == {"model_request": 4.0, "working": 1.5}
	assert got.chat_spans == 0 and got.usage == Usage(0, 0, None, None)


def test_a_counter_that_restarts_keeps_what_it_counted(tmp_path):
	# A second meter provider in the same file, as after a restart: its
	# cumulative counts start again from zero.
	write(
		tmp_path / "metrics.jsonl",
		export(calls(10, 3, start=1.0)),
		export(calls(11, 5, start=1.0)),
		export(calls(20, 2, start=15.0)),
	)
	assert read_export(tmp_path).calls == 7


def test_delta_temporality_is_refused(tmp_path):
	write(
		tmp_path / "metrics.jsonl",
		export(histogram("flowgentic.fleet.call.duration", 10, (FLEET, 1, 0.5), temporality=1)),
	)
	with pytest.raises(ValueError, match="cumulative"):
		read_export(tmp_path)


def test_a_line_that_is_not_json_names_its_file_and_line(tmp_path):
	path = write(tmp_path / "p3" / "metrics.jsonl", export(calls(10, 1)))
	with path.open("a") as f:
		f.write('{"resource_metrics": [{"res')  # a process killed mid-write
	with pytest.raises(ValueError, match=r"p3/metrics.jsonl, line 2"):
		read_export(tmp_path)


# --- checks -------------------------------------------------------------------------


def test_a_complete_export_has_no_problems(tmp_path):
	write(tmp_path / "metrics.jsonl", export(calls(10, 2, failed=1)))
	write(tmp_path / "spans.jsonl", chat(), chat(usage=(50, 5, 32, 2)), chat(ok=False))
	got = read_export(tmp_path)
	assert got.check() == []
	assert (got.chat_spans, got.failed_chat_spans) == (3, 1)
	assert got.usage == Usage(150, 15, 32, 2)


def test_check_finds_what_is_missing(tmp_path):
	assert read_export(tmp_path).check() == [f"no metrics.jsonl under {tmp_path}"]

	write(tmp_path / "p0" / "metrics.jsonl", export(calls(10, 3)))
	write(tmp_path / "p0" / "spans.jsonl", chat(), chat())  # one dropped
	write(tmp_path / "p1" / "metrics.jsonl", export(gauge("x", 10, ({}, 1)), scope="other"))
	write(tmp_path / "p2" / "spans.jsonl", chat())
	problems = read_export(tmp_path).check()
	assert problems == [
		f"{tmp_path / 'p0'}: 2 of the fleet's chat spans for 3 of its calls (0 failed spans "
		"for 0 failed calls): spans were dropped or not exported",
		f"{tmp_path / 'p1' / 'metrics.jsonl'}: no Flowgentic metrics",
		f"{tmp_path / 'p2'}: spans.jsonl without metrics.jsonl",
	]


def test_a_process_without_spans_is_not_checked_against_them(tmp_path):
	write(tmp_path / "metrics.jsonl", export(calls(10, 3)))
	assert read_export(tmp_path).check() == []


def test_a_process_whose_chat_spans_are_langchains_is_not_checked_against_them(tmp_path):
	# LangChain replicas with LangChain's instrumentation on: its spans take
	# the fleet's place, one per try.
	write(tmp_path / "metrics.jsonl", export(calls(10, 2)))
	write(tmp_path / "spans.jsonl", chat(fleet=False), chat(fleet=False), chat(fleet=False))
	assert read_export(tmp_path).check() == []


def test_check_servers_compares_the_tokens_and_the_requests(tmp_path):
	write(
		tmp_path / "metrics.jsonl",
		export(
			calls(10, 2, failed=1),
			counter("flowgentic.fleet.retries", 10, ({**FLEET, "error.type": "ServerUnavailable"}, 1)),
		),
	)
	write(tmp_path / "spans.jsonl", chat(), chat(usage=(50, 5, None, None)), chat(ok=False))
	got = read_export(tmp_path)
	assert got.check_servers(prompt_tokens=150, completion_tokens=15, requests=2) == []
	assert got.check_servers(prompt_tokens=150) == []  # only what is given
	assert got.check_servers(prompt_tokens=151, completion_tokens=15, requests=3) == [
		"prompt tokens: 150 on the chat spans, 151 at the servers",
		"successful requests: 2 chat spans, 3 at the servers (the fleets sent 1 "
		"request again)",
	]


def test_check_servers_needs_spans(tmp_path):
	write(tmp_path / "metrics.jsonl", export(calls(10, 2)))
	assert read_export(tmp_path).check_servers(prompt_tokens=10) == [
		"no spans.jsonl to count the chat spans' tokens"
	]


# --- end to end: two agent processes, each with its own export ------------------------


def run_process(out: Path, agents: int, failures: int) -> dict:
	done = subprocess.run(
		[sys.executable, str(PROCESS), str(out), str(agents), str(failures)],
		capture_output=True,
		text=True,
		check=False,
		timeout=120,
	)
	assert done.returncode == 0, done.stderr
	return json.loads(done.stdout.strip().splitlines()[-1])


def test_two_agent_processes_exports_read_back_and_check(tmp_path):
	runs = [run_process(tmp_path / "p000", 150, 0), run_process(tmp_path / "p001", 100, 2)]
	assert [r["outcomes"] for r in runs] == [{"done": 150}, {"done": 100}]

	got = read_export(tmp_path)
	assert got.check() == []
	assert got.processes == 2 and got.exports >= 2
	assert got.calls == 250 and got.failed_calls == 0
	assert got.chat_spans == 250 and got.failed_chat_spans == 0
	assert got.retries == 2 and got.requests == 252
	assert got.outages == 2 and got.gave_up == 0
	assert got.usage == Usage(25_000, 2_500, 16_000, 1_000)
	assert sum(got.agents[-1][1].values()) == 0  # every run over at the last export
	assert got.agent_seconds["model_request"] > 0
	totals = {k: sum(r[k] for r in runs) for k in ("prompt_tokens", "completion_tokens")}
	assert totals == {"prompt_tokens": 25_000, "completion_tokens": 2_500}
	assert got.check_servers(prompt_tokens=25_000, completion_tokens=2_500, requests=250) == []
