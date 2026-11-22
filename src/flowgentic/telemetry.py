"""Read Flowgentic's OpenTelemetry export back, one per process, and check it.

A campaign's agent processes each write their own export: an application
configures the OpenTelemetry SDK in each process to write two files into a
directory of the process's own, as the telemetry page shows:

- ``metrics.jsonl``: one line per export of the SDK's console metric exporter
  (``MetricsData.to_json()``), with cumulative temporality, the SDK's default;
- ``spans.jsonl``: one line per span of its console span exporter
  (``ReadableSpan.to_json()``), if the process exports spans.

``read_export(path)`` reads every such pair under ``path`` and merges them
into an ``Export``: Flowgentic's counters and histograms summed over the
processes, the agents in each state over time, and the ``chat`` spans with
their usage. ``Export.check()`` says what is missing from the export or does
not add up within it, spans sampled or not; ``Export.check_servers()``
compares it with the model servers' own counters.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from flowgentic.fleet._transport import Usage

__all__ = ["Export", "read_export"]

METRICS = "metrics.jsonl"
SPANS = "spans.jsonl"
_SCOPE = "flowgentic"
_DELTA = 1  # AggregationTemporality.DELTA, as the SDK writes it
_NS = 1e9

_AGENTS = "flowgentic.agents"
_AGENT_TIME = "flowgentic.agent.time"
_CALLS = "flowgentic.fleet.call.duration"
_REQUESTS = "flowgentic.fleet.request.duration"
_RETRIES = "flowgentic.fleet.retries"
_OUTAGES = "flowgentic.fleet.outage.duration"
_TOKENS = "flowgentic.fleet.tokens"
_TOKEN_TYPE = "gen_ai.token.type"  # nosec B105  # an attribute name
_STATE = "flowgentic.agent.state"
_OUTCOME = "flowgentic.fleet.outage.outcome"
_REPLICA = "flowgentic.fleet.replica"
_FLEET = "flowgentic.fleet.name"
_INSTANCE = "service.instance.id"
_ERROR = "error.type"


@dataclass(frozen=True)
class _Part:
	"""One directory, a process's or more: what ``check`` compares within it."""

	directory: Path
	metrics: Path | None
	spans: Path | None
	flowgentic: bool = False  # its metrics hold Flowgentic's
	calls: Mapping[str, tuple[int, int]] = field(default_factory=dict)
	"""By fleet name: its calls, and the failed ones."""
	fleet_spans: Mapping[str, tuple[int, int]] = field(default_factory=dict)
	"""By fleet name: its ``chat`` spans, and the failed ones."""
	other_spans: int = 0
	"""``chat`` spans of no fleet."""
	cut: tuple[Path, ...] = ()
	"""Its files whose last line was cut short."""
	two_writers: bool = False


@dataclass(frozen=True)
class Export:
	"""Flowgentic's telemetry from every process under ``path``, merged.

	Counts are summed over the processes, each process's at its last export.
	``agents`` is the agents in each state over time: at each export's time,
	in seconds since the epoch, each process's latest counts summed.
	``usage`` sums the usage on the ``chat`` spans that did not fail, those of
	the fleet and any other recorder's; a cached or reasoning count is
	``None`` if no span carried one."""

	path: Path
	processes: int
	"""The processes whose metrics were read: in each ``metrics.jsonl``, its
	writers, told apart by their resource's ``service.instance.id``, which
	the SDK sets to a random id unless the application gives one."""
	exports: int
	"""Their exports that held Flowgentic's metrics."""
	agents: tuple[tuple[float, Mapping[str, float]], ...]
	"""``(time, {state: agents})`` at each export's time
	(``flowgentic.agents``)."""
	agent_seconds: Mapping[str, float]
	"""Agent-seconds by state, of every agent run (``flowgentic.agent.time``)."""
	calls: int
	"""Model calls through the fleets, failed ones included
	(``flowgentic.fleet.call.duration``)."""
	failed_calls: int
	requests: int
	"""Requests to the replicas, a call's every try
	(``flowgentic.fleet.request.duration``)."""
	retries: int
	"""Requests sent again once their replica came back
	(``flowgentic.fleet.retries``)."""
	outages: int
	"""Replica outages that ``Recovery`` waited out
	(``flowgentic.fleet.outage.duration``, ``recovered``)."""
	gave_up: int
	"""Replica outages that outlasted ``Recovery``'s ``outage_s``."""
	down_s: float
	"""The outages' time, both kinds."""
	chat_spans: int
	"""``chat`` spans, failed ones included."""
	failed_chat_spans: int
	usage: Usage
	"""On the ``chat`` spans that did not fail."""
	fleet_usage: Usage | None = None
	"""The prompt and completion tokens the servers counted for the fleets'
	calls (``flowgentic.fleet.tokens``), sampled spans or not; ``None`` if the
	export has no such counter."""
	_parts: tuple[_Part, ...] = field(default=(), repr=False, compare=False)

	def check(self, *, sampled: bool = False) -> list[str]:
		"""What is missing from the export, or does not add up within it; an
		empty list if nothing.

		Missing: a ``metrics.jsonl`` under ``path``, Flowgentic's metrics in
		one, the ``metrics.jsonl`` beside a ``spans.jsonl``, or a file's last
		line, cut short as a process that ended while writing leaves it.

		Not adding up: in a directory with both files, each fleet's ``chat``
		spans, and its failed ones, against its calls and its failed calls.
		The fleet records one span per call, so a gap means spans were
		dropped, as a ``BatchSpanProcessor`` whose queue is full drops them,
		or never exported. A fleet with no span of its own, in a directory
		with ``chat`` spans of another recorder, such as LangChain's
		instrumentation with LangChain replicas, is not compared. Nor is it
		sound when two processes wrote one series under one resource, which
		this reports too.

		``sampled``: the spans were sampled, as with
		``ParentBased(TraceIdRatioBased(r))``, so fewer spans than calls is
		expected and only more spans than calls is a problem. The counts come
		from the metrics, which sampling does not touch.
		"""
		if not self._parts or all(p.metrics is None for p in self._parts):
			return [f"no {METRICS} under {self.path}"]
		problems = []
		for part in self._parts:
			for path in part.cut:
				problems.append(
					f"{path}: its last line is cut short, as a process that ended "
					"while writing leaves it; read without it"
				)
			if part.metrics is None:
				problems.append(f"{part.directory}: {SPANS} without {METRICS}")
				continue
			if part.two_writers:
				problems.append(
					f"{part.metrics}: two processes wrote the same series with one "
					"resource: give each process its own service.instance.id, or a "
					"directory of its own"
				)
			if not part.flowgentic:
				problems.append(f"{part.metrics}: no Flowgentic metrics")
				continue
			if part.spans is None:
				continue
			for name in sorted(set(part.calls) | set(part.fleet_spans)):
				spans = part.fleet_spans.get(name, (0, 0))
				calls = part.calls.get(name, (0, 0))
				if spans == (0, 0) and part.other_spans:
					continue  # another recorder's spans in its place
				if sampled:
					if spans[0] <= calls[0] and spans[1] <= calls[1]:
						continue
					why = "more spans than calls"
				elif spans == calls:
					continue
				else:
					why = "spans were dropped or not exported"
				problems.append(
					f"{part.directory}: fleet {name!r}: {spans[0]} chat spans for "
					f"{calls[0]} calls ({spans[1]} failed spans for {calls[1]} "
					f"failed calls): {why}"
				)
		return problems

	def check_servers(
		self,
		*,
		prompt_tokens: int | None = None,
		completion_tokens: int | None = None,
		requests: int | None = None,
	) -> list[str]:
		"""Compare the export with the model servers' own counters over the
		run, as vLLM's ``prompt_tokens_total``, ``generation_tokens_total`` and
		``request_success_total`` count them; an empty list if they agree.

		The fleets' own counts give the prompt and completion tokens
		(``flowgentic.fleet.tokens``) and the successful requests (the calls
		that did not fail), so sampled spans change nothing; calls made outside
		a fleet, as the agent loop makes on a bare transport, are then not
		counted. An export whose fleets made no calls, or without the fleets'
		token counter, is compared through all its ``chat`` spans that did not
		fail. Only what is given is compared. A request the fleet gave up on
		that the server still finished, as after a client's timeout, counts at
		the server only: the difference names the requests sent again."""
		if self.fleet_usage is not None and self.calls:
			source = "in the fleets' metrics"
			usage = self.fleet_usage
			ok = self.calls - self.failed_calls
		elif any(p.spans for p in self._parts):
			source = "on the chat spans"
			usage = self.usage
			ok = self.chat_spans - self.failed_chat_spans
		else:
			return [f"no {_TOKENS} and no {SPANS}: nothing to compare with the servers"]
		problems = []
		pairs = (
			("prompt tokens", usage.prompt_tokens, prompt_tokens),
			("completion tokens", usage.completion_tokens, completion_tokens),
			("successful requests", ok, requests),
		)
		for what, ours, theirs in pairs:
			if theirs is None or ours == theirs:
				continue
			problem = f"{what}: {ours} {source}, {theirs} at the servers"
			if what == "successful requests":
				again = "request" if self.retries == 1 else "requests"
				problem += f" (the fleets sent {self.retries} {again} again)"
			problems.append(problem)
		return problems


def read_export(path: str | Path) -> Export:
	"""Read and merge every process's export under ``path``: each directory
	that holds a ``metrics.jsonl``, a ``spans.jsonl`` or both, best one per
	process. Processes that write into one file are told apart by their
	resource and their series' start times.

	A last line cut short, with no newline after it, is left out, and
	``check()`` reports it. Raises ``ValueError`` for any other line that is
	not JSON, naming its file and line, and for a metric exported with delta
	temporality, which this does not sum."""
	root = Path(path)
	directories = sorted(
		{p.parent for name in (METRICS, SPANS) for p in root.rglob(name)}
	)
	parts: list[_Part] = []
	series: list[list[tuple[float, dict[str, float]]]] = []
	totals: Counter[str] = Counter()
	seconds: Counter[str] = Counter()
	outages: Counter[str] = Counter()
	tokens: Counter[str] | None = None
	down_s = 0.0
	exports = processes = 0
	spans = _Spans()
	for directory in directories:
		metrics = directory / METRICS
		span_file = directory / SPANS
		part = _Part(
			directory,
			metrics if metrics.exists() else None,
			span_file if span_file.exists() else None,
		)
		cut: list[Path] = []
		if part.metrics is not None:
			read = _read_metrics(part.metrics, cut)
			exports += read.exports
			processes += len(read.agents)
			series.extend(read.agents.values())
			totals.update(read.totals)
			seconds.update(read.seconds)
			outages.update(read.outages)
			if read.tokens:
				tokens = (tokens or Counter()) + read.tokens
			down_s += read.down_s
			part = replace(
				part,
				flowgentic=read.exports > 0,
				calls={
					name: (int(n), int(read.failed_by_fleet[name]))
					for name, n in read.calls_by_fleet.items()
				},
				two_writers=read.two_writers,
			)
		if part.spans is not None:
			by_fleet, other = spans.read(part.spans, cut)
			part = replace(part, fleet_spans=by_fleet, other_spans=other)
		parts.append(replace(part, cut=tuple(cut)))
	return Export(
		path=root,
		processes=processes,
		exports=exports,
		agents=_merge(series),
		agent_seconds=dict(seconds),
		calls=int(totals["calls"]),
		failed_calls=int(totals["failed_calls"]),
		requests=int(totals["requests"]),
		retries=int(totals["retries"]),
		outages=int(outages["recovered"]),
		gave_up=int(outages["gave_up"]),
		down_s=down_s,
		chat_spans=spans.chat,
		failed_chat_spans=spans.failed,
		usage=spans.usage(),
		fleet_usage=None
		if tokens is None
		else Usage(int(tokens["input"]), int(tokens["output"])),
		_parts=tuple(parts),
	)


def _lines(path: Path, cut: list[Path]) -> Iterator[tuple[int, dict]]:
	"""Each line of ``path`` as JSON, with its number. A last line with no
	newline after it that is not JSON was cut short: it is left out, and
	``path`` added to ``cut``."""
	with path.open() as f:
		for n, raw in enumerate(f, 1):
			line = raw.strip()
			if not line:
				continue
			try:
				yield n, json.loads(line)
			except json.JSONDecodeError as exc:
				if not raw.endswith("\n"):  # only a file's last line ends so
					cut.append(path)
					return
				raise ValueError(f"{path}, line {n}: not JSON ({exc.msg})") from None


@dataclass
class _Metrics:
	"""One ``metrics.jsonl``'s metrics: its exports, each writer's agents over
	time, and the totals of its cumulative instruments at their last
	export."""

	exports: int = 0
	agents: dict[object, list[tuple[float, dict[str, float]]]] = field(
		default_factory=dict
	)
	"""By writer, its resource: its agents in each state at each export."""
	totals: Counter[str] = field(default_factory=Counter)
	seconds: Counter[str] = field(default_factory=Counter)
	outages: Counter[str] = field(default_factory=Counter)
	down_s: float = 0.0
	tokens: Counter[str] = field(default_factory=Counter)
	calls_by_fleet: Counter[str] = field(default_factory=Counter)
	failed_by_fleet: Counter[str] = field(default_factory=Counter)
	two_writers: bool = False


def _read_metrics(path: Path, cut: list[Path]) -> _Metrics:
	"""A cumulative series counts from zero again when its meter provider
	starts again, under a new start time: its total is its last value at each
	start time, summed. So it is too when two processes write it under one
	resource, which shows as a start time that comes back after another."""
	out = _Metrics()
	# (writer, metric, attributes) -> {start time: (count or value, sum)}
	series: dict[tuple, dict] = {}
	last_start: dict[tuple, object] = {}
	for n, line in _lines(path, cut):
		seen = False
		snapshots: dict[object, tuple[float, dict[str, float]]] = {}
		for writer, name, data, point in _points(line):
			seen = True
			t = point.get("time_unix_nano", 0) / _NS
			attrs = point.get("attributes") or {}
			if name == _AGENTS:
				state = attrs.get(_STATE, "?")
				_, counts = snapshots.setdefault(writer, (t, {}))
				counts[state] = counts.get(state, 0) + point.get("value", 0)
				continue
			if data.get("aggregation_temporality") == _DELTA:
				raise ValueError(
					f"{path}, line {n}: {name} is exported with delta temporality; "
					"read_export sums cumulative exports, the SDK's default"
				)
			value = (point.get("count", point.get("value", 0)), point.get("sum", 0.0))
			key = (writer, name, frozenset((k, str(v)) for k, v in attrs.items()))
			starts = series.setdefault(key, {})
			start = point.get("start_time_unix_nano")
			if start in starts and last_start.get(key) != start:
				out.two_writers = True
			starts[start] = value
			last_start[key] = start
		if not seen:
			continue
		out.exports += 1
		for writer, snapshot in snapshots.items():
			out.agents.setdefault(writer, []).append(snapshot)
	for writer in {key[0] for key in series}:  # writers without agents count too
		out.agents.setdefault(writer, [])
	for (_, name, attrs), starts in series.items():
		count = sum(v[0] for v in starts.values())
		total = sum(v[1] for v in starts.values())
		a = dict(attrs)
		if name == _CALLS:
			fleet = a.get(_FLEET, "")
			out.totals["calls"] += count
			out.calls_by_fleet[fleet] += count
			if _ERROR in a:
				out.totals["failed_calls"] += count
				out.failed_by_fleet[fleet] += count
		elif name == _REQUESTS:
			out.totals["requests"] += count
		elif name == _RETRIES:
			out.totals["retries"] += count
		elif name == _OUTAGES:
			out.outages[a.get(_OUTCOME, "?")] += count
			out.down_s += total
		elif name == _AGENT_TIME:
			out.seconds[a.get(_STATE, "?")] += count  # a counter's value
		elif name == _TOKENS:
			out.tokens[a.get(_TOKEN_TYPE, "?")] += count
	return out


def _writer(resource: dict) -> object:
	"""The process that wrote an export: its ``service.instance.id``, or else
	its whole resource."""
	attrs = (resource.get("resource") or {}).get("attributes") or {}
	instance = attrs.get(_INSTANCE)
	if instance is not None:
		return instance
	return tuple(sorted((k, str(v)) for k, v in attrs.items()))


def _points(line: dict) -> Iterator[tuple[object, str, dict, dict]]:
	"""Flowgentic's data points in one export: (writer, metric, its data,
	point)."""
	for resource in line.get("resource_metrics") or ():
		writer = _writer(resource)
		for scope in resource.get("scope_metrics") or ():
			if (scope.get("scope") or {}).get("name") != _SCOPE:
				continue
			for metric in scope.get("metrics") or ():
				data = metric.get("data") or {}
				for point in data.get("data_points") or ():
					yield writer, metric.get("name"), data, point


def _merge(
	series: list[list[tuple[float, dict[str, float]]]],
) -> tuple[tuple[float, Mapping[str, float]], ...]:
	"""At each export's time, each process's latest counts, summed."""
	series = [s for s in series if s]
	times = sorted({t for s in series for t, _ in s})
	at = [0] * len(series)
	latest: list[dict[str, float]] = [{} for _ in series]
	out = []
	for t in times:
		total: dict[str, float] = {}
		for i, s in enumerate(series):
			while at[i] < len(s) and s[at[i]][0] <= t:
				latest[i] = s[at[i]][1]
				at[i] += 1
			for state, n in latest[i].items():
				total[state] = total.get(state, 0) + n
		out.append((t, total))
	return tuple(out)


class _Spans:
	"""The ``chat`` spans read so far: the fleets', failed or not, and any
	other recorder's, with the usage of those that did not fail."""

	def __init__(self) -> None:
		self.chat = self.failed = 0
		self.tokens: Counter[str] = Counter()
		self.reported: set[str] = set()

	def read(
		self, path: Path, cut: list[Path]
	) -> tuple[dict[str, tuple[int, int]], int]:
		"""Read one ``spans.jsonl``; return its fleets' spans and failed
		spans, by fleet name, and its ``chat`` spans of no fleet."""
		spans: Counter[str] = Counter()
		failures: Counter[str] = Counter()
		other = 0
		for _, span in _lines(path, cut):
			attrs = span.get("attributes") or {}
			if attrs.get("gen_ai.operation.name") != "chat":
				continue
			failed = (span.get("status") or {}).get(
				"status_code"
			) == "ERROR" or _ERROR in attrs
			self.chat += 1
			self.failed += failed
			if _REPLICA in attrs:
				fleet = str(attrs.get(_FLEET, ""))
				spans[fleet] += 1
				failures[fleet] += failed
			else:
				other += 1
			if failed:
				continue
			for key in _USAGE:
				value = attrs.get(key)
				if value is not None:
					self.tokens[key] += int(value)
					self.reported.add(key)
		return {name: (n, failures[name]) for name, n in spans.items()}, other

	def usage(self) -> Usage:
		def optional(key: str) -> int | None:
			return self.tokens[key] if key in self.reported else None

		return Usage(
			self.tokens[_INPUT],
			self.tokens[_OUTPUT],
			optional(_CACHED),
			optional(_REASONING),
		)


_INPUT = "gen_ai.usage.input_tokens"
_OUTPUT = "gen_ai.usage.output_tokens"
_CACHED = "gen_ai.usage.cache_read.input_tokens"
_REASONING = "gen_ai.usage.reasoning.output_tokens"
_USAGE = (_INPUT, _OUTPUT, _CACHED, _REASONING)
