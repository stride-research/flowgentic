"""The fleet's telemetry, with no LangChain: one ``chat`` span per call, with
its usage and its wait for admission, and the counters of the calls, of the
requests sent again and of the replicas' outages."""

# ruff: noqa: S101

import asyncio

import pytest
from _basics import ScriptedTransport, reply

from flowgentic.fleet import Fleet, Recovery, RequestAdmission, ServerUnavailable


def chat_spans(exporter):
	return [
		s
		for s in exporter.get_finished_spans()
		if s.attributes.get("gen_ai.operation.name") == "chat"
	]


def points(reader, name, fleet):
	"""The data points of metric ``name`` that carry fleet name ``fleet``."""
	out = []
	data = reader.get_metrics_data()
	for resource in data.resource_metrics if data else ():
		for scope in resource.scope_metrics:
			for metric in scope.metrics:
				if metric.name == name:
					out.extend(
						p
						for p in metric.data.data_points
						if p.attributes.get("flowgentic.fleet.name") == fleet
					)
	return out


def by_error(reader, name, fleet, field="count"):
	"""A metric's ``field`` per ``error.type``, ``None`` for success."""
	out = {}
	for p in points(reader, name, fleet):
		key = p.attributes.get("error.type")
		out[key] = out.get(key, 0) + getattr(p, field)
	return out


class Flaky(ScriptedTransport):
	"""A transport whose server is down for its first ``failures`` calls."""

	def __init__(self, failures, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.failures = failures

	async def complete(self, messages, tools=None, **kwargs):
		if self.failures > 0:
			self.failures -= 1
			raise ServerUnavailable("connection refused")
		return await super().complete(messages, tools, **kwargs)


def usage_reply(messages):
	return reply("DONE", usage=(300, 20), cached=256, thinking=12)


async def test_each_call_is_one_chat_span_with_its_usage_and_its_wait(
	span_exporter, metric_reader
):
	transport = ScriptedTransport(
		usage_reply, base_url="http://node-3:8100/v1", latency_s=0.02
	)
	fleet = Fleet([transport], admission=RequestAdmission(1), name="spans")
	first = [{"role": "user", "content": "hi"}]
	await asyncio.gather(*(fleet.ainvoke(f"a{i}", first) for i in range(3)))
	later = [
		*first,
		{"role": "assistant", "content": "", "tool_calls": [
			{"id": "c1", "type": "function", "function": {"name": "simulate", "arguments": "{}"}}
		]},
		{"role": "tool", "tool_call_id": "c1", "content": "0.98"},
	]  # fmt: skip
	await fleet.ainvoke("a0", later)

	spans = chat_spans(span_exporter)
	assert len(spans) == 4  # one per call
	for span in spans:
		a = span.attributes
		assert span.name == "chat scripted"
		assert span.kind.name == "CLIENT"
		assert a["gen_ai.request.model"] == "scripted"
		assert a["gen_ai.response.model"] == "scripted"
		assert a["server.address"] == "node-3" and a["server.port"] == 8100
		assert a["flowgentic.fleet.name"] == "spans"
		assert a["flowgentic.fleet.replica"] == 0
		assert a["gen_ai.usage.input_tokens"] == 300
		assert a["gen_ai.usage.output_tokens"] == 20
		assert a["gen_ai.usage.cache_read.input_tokens"] == 256
		assert a["gen_ai.usage.reasoning.output_tokens"] == 12
		assert a["flowgentic.fleet.admission_wait"] >= 0
		assert "flowgentic.fleet.resend_count" not in a
		assert "flowgentic.fleet.recovery_wait" not in a
		assert span.status.status_code.name == "UNSET"
	waits = sorted(s.attributes["flowgentic.fleet.admission_wait"] for s in spans[:3])
	assert waits[-1] >= 0.03  # the third waited for the first two
	# The span covers the call, the wait for admission included.
	for span in spans:
		duration = (span.end_time - span.start_time) / 1e9
		assert duration >= span.attributes["flowgentic.fleet.admission_wait"] + 0.015

	# The agent, its previous call and the tool whose result came just before
	conversations = sorted(s.attributes["gen_ai.conversation.id"] for s in spans)
	assert conversations == ["a0", "a0", "a1", "a2"]
	second = spans[-1].attributes
	assert second["gen_ai.conversation.id"] == "a0"
	assert second["flowgentic.fleet.after_tool"] == "simulate"
	assert second["flowgentic.fleet.gap"] >= 0
	assert all(
		"flowgentic.fleet.gap" not in s.attributes
		and "flowgentic.fleet.after_tool" not in s.attributes
		for s in spans[:3]
	)

	# One call duration per call, as long as the span
	assert by_error(metric_reader, "flowgentic.fleet.call.duration", "spans") == {None: 4}


async def test_a_call_through_an_outage_is_one_span_and_counted(
	span_exporter, metric_reader
):
	transport = Flaky(2, usage_reply, base_url="http://node-3:8100/v1")

	async def check(i):
		return transport  # back at the first check

	recovery = Recovery(check, poll_s=0.01)
	fleet = Fleet([transport], recovery=recovery, name="outage")
	records = []
	fleet.add_listener(lambda record, r: records.append(record))
	await fleet.ainvoke("a", [{"role": "user", "content": "hi"}])

	(span,) = chat_spans(span_exporter)
	a = span.attributes
	assert span.status.status_code.name == "UNSET"
	assert "error.type" not in a
	assert a["flowgentic.fleet.resend_count"] == 2
	assert a["flowgentic.fleet.recovery_wait"] == pytest.approx(
		records[0].recovery_s, abs=1e-6
	)
	assert a["flowgentic.fleet.recovery_wait"] >= 0.01  # one poll_s at least
	assert a["flowgentic.fleet.admission_wait"] == pytest.approx(
		records[0].wait_s, abs=1e-6
	)
	assert a["gen_ai.usage.input_tokens"] == 300

	# Each try is a request; the call is one call; two were sent again.
	requests = by_error(metric_reader, "flowgentic.fleet.request.duration", "outage")
	assert requests == {"ServerUnavailable": 2, None: 1}
	calls = by_error(metric_reader, "flowgentic.fleet.call.duration", "outage")
	assert calls == {None: 1}
	retries = by_error(metric_reader, "flowgentic.fleet.retries", "outage", "value")
	assert retries == {"ServerUnavailable": 2}
	outages = {
		p.attributes["flowgentic.fleet.outage.outcome"]: p.count
		for p in points(metric_reader, "flowgentic.fleet.outage.duration", "outage")
	}
	assert outages == {"recovered": 2}  # one watch per failure, each back at once
	(outage,) = points(metric_reader, "flowgentic.fleet.outage.duration", "outage")
	assert outage.attributes["flowgentic.fleet.replica"] == 0
	assert fleet.stats()["retried_calls"] == 2
	assert recovery.stats()["outages"] == {0: 2}


async def test_a_call_whose_replica_stays_down_is_one_failed_span(
	span_exporter, metric_reader
):
	transport = Flaky(10**6, usage_reply)

	async def check(i):
		return None  # still down

	fleet = Fleet(
		[transport], recovery=Recovery(check, outage_s=0.05, poll_s=0.01), name="gone"
	)
	with pytest.raises(ServerUnavailable):
		await fleet.ainvoke("a", [{"role": "user", "content": "hi"}])

	(span,) = chat_spans(span_exporter)
	assert span.status.status_code.name == "ERROR"
	assert span.attributes["error.type"] == "ServerUnavailable"
	assert span.attributes["flowgentic.fleet.recovery_wait"] >= 0.04
	assert "gen_ai.usage.input_tokens" not in span.attributes
	calls = by_error(metric_reader, "flowgentic.fleet.call.duration", "gone")
	assert calls == {"ServerUnavailable": 1}
	assert points(metric_reader, "flowgentic.fleet.retries", "gone") == []
	await asyncio.sleep(0.05)  # the watch gives up just after the call does
	outages = {
		p.attributes["flowgentic.fleet.outage.outcome"]: p.count
		for p in points(metric_reader, "flowgentic.fleet.outage.duration", "gone")
	}
	assert outages == {"gave_up": 1}


async def test_a_refused_call_is_one_failed_span_and_not_sent_again(
	span_exporter, metric_reader
):
	from flowgentic.fleet import RequestRejected

	class Refusing(ScriptedTransport):
		async def complete(self, messages, tools=None, **kwargs):
			raise RequestRejected("400 bad request", status_code=400)

	async def check(i):
		raise AssertionError("not asked: a refusal is not an outage")

	fleet = Fleet([Refusing(usage_reply)], recovery=Recovery(check), name="refused-call")
	with pytest.raises(RequestRejected):
		await fleet.ainvoke("a", [{"role": "user", "content": "hi"}])
	(span,) = chat_spans(span_exporter)
	assert span.attributes["error.type"] == "RequestRejected"
	assert "flowgentic.fleet.resend_count" not in span.attributes
	assert "flowgentic.fleet.recovery_wait" not in span.attributes
	calls = by_error(metric_reader, "flowgentic.fleet.call.duration", "refused-call")
	assert calls == {"RequestRejected": 1}


async def test_a_cancelled_call_is_one_failed_span_and_counted(
	span_exporter, metric_reader
):
	transport = ScriptedTransport(usage_reply, latency_s=10)
	fleet = Fleet([transport], name="cancelled")
	call = asyncio.create_task(fleet.ainvoke("a", [{"role": "user", "content": "hi"}]))
	await asyncio.sleep(0.01)
	call.cancel()
	with pytest.raises(asyncio.CancelledError):
		await call
	(span,) = chat_spans(span_exporter)
	assert span.status.status_code.name == "ERROR"
	assert span.attributes["error.type"] == "CancelledError"
	calls = by_error(metric_reader, "flowgentic.fleet.call.duration", "cancelled")
	assert calls == {"CancelledError": 1}


async def test_a_replica_that_comes_back_elsewhere_names_its_new_server(span_exporter):
	down = Flaky(1, usage_reply, base_url="http://node-3:8100/v1")
	moved = ScriptedTransport(usage_reply, base_url="http://node-7:8200/v1")

	async def check(i):
		return moved  # a requeued model job, on another node

	fleet = Fleet([down], recovery=Recovery(check, poll_s=0.01), name="moved")
	await fleet.ainvoke("a", [{"role": "user", "content": "hi"}])
	(span,) = chat_spans(span_exporter)
	assert span.attributes["server.address"] == "node-7"
	assert span.attributes["server.port"] == 8200


async def test_the_spans_of_concurrent_calls_through_one_outage_add_up(
	span_exporter, metric_reader
):
	server = {"up": False}

	class Down(ScriptedTransport):
		async def complete(self, messages, tools=None, **kwargs):
			if not server["up"]:
				raise ServerUnavailable("connection refused")
			return await super().complete(messages, tools, **kwargs)

	transport = Down(usage_reply)
	checks = []

	async def check(i):
		checks.append(i)
		if len(checks) < 3:
			return None
		server["up"] = True
		return transport

	fleet = Fleet([transport], recovery=Recovery(check, poll_s=0.01), name="shared")
	await asyncio.gather(
		*(fleet.ainvoke(f"a{i}", [{"role": "user", "content": "hi"}]) for i in range(5))
	)
	spans = chat_spans(span_exporter)
	assert len(spans) == 5
	assert all(s.attributes["flowgentic.fleet.resend_count"] == 1 for s in spans)
	assert sum(by_error(metric_reader, "flowgentic.fleet.retries", "shared", "value").values()) == 5
	outages = points(metric_reader, "flowgentic.fleet.outage.duration", "shared")
	assert [p.count for p in outages] == [1]  # one watch served every call
	assert outages[0].sum >= 0.02  # two polls down


def tokens(reader, fleet):
	"""``flowgentic.fleet.tokens`` of fleet ``fleet``: {(replica, type): value}."""
	return {
		(p.attributes["flowgentic.fleet.replica"], p.attributes["gen_ai.token.type"]): p.value
		for p in points(reader, "flowgentic.fleet.tokens", fleet)
	}


async def test_the_fleets_tokens_are_counted_when_the_sdk_collects(metric_reader):
	from flowgentic.fleet import RoundRobin

	fleet = Fleet(
		[ScriptedTransport(usage_reply), ScriptedTransport(usage_reply)],
		routing=RoundRobin(),
		name="tokens",
	)
	for agent in ("a", "b", "c"):
		await fleet.ainvoke(agent, [{"role": "user", "content": "hi"}])
	assert tokens(metric_reader, "tokens") == {
		(0, "input"): 600,
		(0, "output"): 40,
		(1, "input"): 300,
		(1, "output"): 20,
	}


async def test_a_fleets_tokens_stay_counted_after_it_is_gone(metric_reader):
	import gc

	fleet = Fleet([ScriptedTransport(usage_reply)], name="gone-tokens")
	await fleet.ainvoke("a", [{"role": "user", "content": "hi"}])
	del fleet
	gc.collect()
	# A counter only grows: the process keeps what its fleets counted.
	assert tokens(metric_reader, "gone-tokens") == {(0, "input"): 300, (0, "output"): 20}
