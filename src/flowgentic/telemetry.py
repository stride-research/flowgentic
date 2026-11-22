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
_TOKEN_TYPE = "gen_ai.token.type"
_STATE = "flowgentic.agent.state"
_OUTCOME = "flowgentic.fleet.outage.outcome"
_REPLICA = "flowgentic.fleet.replica"
_ERROR = "error.type"


@dataclass(frozen=True)
class _Part:
	"""One process's directory: what ``check`` compares within it."""

	directory: Path
	metrics: Path | None
	spans: Path | None
	flowgentic: bool = False  # its metrics hold Flowgentic's
	calls: int = 0
	failed_calls: int = 0
	fleet_spans: int = 0
	failed_fleet_spans: int = 0
	other_spans: int = 0


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
	"""The ``metrics.jsonl`` files read, one per process."""
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
		one, or the ``metrics.jsonl`` beside a ``spans.jsonl``.

		Not adding up: in a process that wrote both files, the fleet's
		``chat`` spans, and its failed ones, against its calls and its failed
		calls. The fleet records one span per call, so a gap means spans were
		dropped, as a ``BatchSpanProcessor`` whose queue is full drops them,
		or never exported. A process whose ``chat`` spans all come from
		another recorder, such as LangChain's instrumentation with LangChain
		replicas, is not compared.

		``sampled``: the spans were sampled, as with
		``ParentBased(TraceIdRatioBased(r))``, so fewer spans than calls is
		expected and only more spans than calls is a problem. The counts come
		from the metrics, which sampling does not touch.
		"""
		if not self._parts or all(p.metrics is None for p in self._parts):
			return [f"no {METRICS} under {self.path}"]
		problems = []
		for part in self._parts:
			if part.metrics is None:
				problems.append(f"{part.directory}: {SPANS} without {METRICS}")
				continue
			if not part.flowgentic:
				problems.append(f"{part.metrics}: no Flowgentic metrics")
				continue
			if part.spans is None or (part.fleet_spans == 0 and part.other_spans):
				continue
			spans = (part.fleet_spans, part.failed_fleet_spans)
			calls = (part.calls, part.failed_calls)
			if sampled:
				if spans[0] <= calls[0] and spans[1] <= calls[1]:
					continue
				why = "more spans than calls"
			elif spans == calls:
				continue
			else:
				why = "spans were dropped or not exported"
			problems.append(
				f"{part.directory}: {spans[0]} of the fleet's chat spans for "
				f"{calls[0]} of its calls ({spans[1]} failed spans for {calls[1]} "
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
		that did not fail), so sampled spans change nothing. An export
		without the fleets' token counter, or whose calls went outside a
		fleet, is compared through its ``chat`` spans that did not fail. Only
		what is given is compared. A request the fleet gave up on that the
		server still finished, as after a client's timeout, counts at the
		server only: the difference names the requests sent again."""
		if self.fleet_usage is not None:
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
	that holds a ``metrics.jsonl``, a ``spans.jsonl`` or both.

	Raises ``ValueError`` for a line that is not JSON, naming its file and
	line, as a process killed while writing leaves one, and for a metric
	exported with delta temporality, which this does not sum."""
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
	exports = 0
	spans = _Spans()
	for directory in directories:
		metrics = directory / METRICS
		span_file = directory / SPANS
		part = _Part(
			directory,
			metrics if metrics.exists() else None,
			span_file if span_file.exists() else None,
		)
		if part.metrics is not None:
			read = _read_metrics(part.metrics)
			exports += read.exports
			series.append(read.agents)
			totals.update(read.totals)
			seconds.update(read.seconds)
			outages.update(read.outages)
			if read.tokens:
				tokens = (tokens or Counter()) + read.tokens
			down_s += read.down_s
			part = replace(
				part,
				flowgentic=read.exports > 0,
				calls=read.totals["calls"],
				failed_calls=read.totals["failed_calls"],
			)
		if part.spans is not None:
			before = spans.snapshot()
			spans.read(part.spans)
			after = spans.snapshot()
			part = replace(
				part,
				fleet_spans=after[0] - before[0],
				failed_fleet_spans=after[1] - before[1],
				other_spans=after[2] - before[2],
			)
		parts.append(part)
	return Export(
		path=root,
		processes=sum(p.metrics is not None for p in parts),
		exports=exports,
		agents=_merge(series),
		agent_seconds=dict(seconds),
		calls=totals["calls"],
		failed_calls=totals["failed_calls"],
		requests=totals["requests"],
		retries=totals["retries"],
		outages=outages["recovered"],
		gave_up=outages["gave_up"],
		down_s=down_s,
		chat_spans=spans.chat,
		failed_chat_spans=spans.failed,
		usage=spans.usage(),
		fleet_usage=None
		if tokens is None
		else Usage(int(tokens["input"]), int(tokens["output"])),
		_parts=tuple(parts),
	)


def _lines(path: Path) -> Iterator[dict]:
	with path.open() as f:
		for n, line in enumerate(f, 1):
			line = line.strip()
			if not line:
				continue
			try:
				yield json.loads(line)
			except json.JSONDecodeError as exc:
				raise ValueError(f"{path}, line {n}: not JSON ({exc.msg})") from None


@dataclass
class _Metrics:
	"""One process's metrics: its exports, its agents over time, and the
	totals of its cumulative instruments at its last export."""

	exports: int = 0
	agents: list[tuple[float, dict[str, float]]] = field(default_factory=list)
	totals: Counter[str] = field(default_factory=Counter)
	seconds: Counter[str] = field(default_factory=Counter)
	outages: Counter[str] = field(default_factory=Counter)
	down_s: float = 0.0
	tokens: Counter[str] = field(default_factory=Counter)


class _Cumulative:
	"""A cumulative series' total, across the restarts of its meter provider:
	a new start time means it counts from zero again."""

	__slots__ = ("start", "value", "banked")

	def __init__(self) -> None:
		self.start: int | None = None
		self.value: tuple[float, float] = (0, 0.0)
		self.banked: tuple[float, float] = (0, 0.0)

	def add(self, start: int | None, value: tuple[float, float]) -> None:
		if self.start is not None and start != self.start:
			self.banked = (
				self.banked[0] + self.value[0],
				self.banked[1] + self.value[1],
			)
		self.start = start
		self.value = value

	def total(self) -> tuple[float, float]:
		return (self.banked[0] + self.value[0], self.banked[1] + self.value[1])


def _read_metrics(path: Path) -> _Metrics:
	out = _Metrics()
	series: dict[tuple[str, frozenset], _Cumulative] = {}
	for n, line in enumerate(_lines(path), 1):
		t = None
		agents: dict[str, float] | None = None
		for name, data, point in _points(line):
			t = point.get("time_unix_nano", 0) / _NS
			attrs = point.get("attributes") or {}
			if name == _AGENTS:
				agents = {} if agents is None else agents
				state = attrs.get(_STATE, "?")
				agents[state] = agents.get(state, 0) + point.get("value", 0)
				continue
			if data.get("aggregation_temporality") == _DELTA:
				raise ValueError(
					f"{path}, line {n}: {name} is exported with delta temporality; "
					"read_export sums cumulative exports, the SDK's default"
				)
			value = (point.get("count", point.get("value", 0)), point.get("sum", 0.0))
			key = (name, frozenset((k, str(v)) for k, v in attrs.items()))
			series.setdefault(key, _Cumulative()).add(
				point.get("start_time_unix_nano"), value
			)
		if t is None:
			continue
		out.exports += 1
		if agents is not None:
			out.agents.append((t, agents))
	for (name, attrs), cumulative in series.items():
		count, total = cumulative.total()
		a = dict(attrs)
		if name == _CALLS:
			out.totals["calls"] += count
			if _ERROR in a:
				out.totals["failed_calls"] += count
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


def _points(line: dict) -> Iterator[tuple[str, dict, dict]]:
	"""Flowgentic's data points in one export: (metric, its data, point)."""
	for resource in line.get("resource_metrics") or ():
		for scope in resource.get("scope_metrics") or ():
			if (scope.get("scope") or {}).get("name") != _SCOPE:
				continue
			for metric in scope.get("metrics") or ():
				data = metric.get("data") or {}
				for point in data.get("data_points") or ():
					yield metric.get("name"), data, point


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
	"""The ``chat`` spans read so far: the fleet's, failed or not, and any
	other recorder's, with the usage of those that did not fail."""

	def __init__(self) -> None:
		self.chat = self.failed = 0
		self.fleet = self.failed_fleet = self.other = 0
		self.tokens: Counter[str] = Counter()
		self.reported: set[str] = set()

	def snapshot(self) -> tuple[int, int, int]:
		return self.fleet, self.failed_fleet, self.other

	def read(self, path: Path) -> None:
		for span in _lines(path):
			attrs = span.get("attributes") or {}
			if attrs.get("gen_ai.operation.name") != "chat":
				continue
			failed = (span.get("status") or {}).get(
				"status_code"
			) == "ERROR" or _ERROR in attrs
			self.chat += 1
			self.failed += failed
			if _REPLICA in attrs:
				self.fleet += 1
				self.failed_fleet += failed
			else:
				self.other += 1
			if failed:
				continue
			for key in _USAGE:
				value = attrs.get(key)
				if value is not None:
					self.tokens[key] += int(value)
					self.reported.add(key)

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
