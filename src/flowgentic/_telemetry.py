"""Flowgentic's OpenTelemetry names, tracer, logger and trace-context carrier.

Flowgentic depends on the OpenTelemetry API only: without an SDK configured by
the application, every span and event here is a no-op. The GenAI names follow
``open-telemetry/semantic-conventions-genai`` at commit ``9ffc7e0``
(2026-10-21), which has no release yet; a change of conventions is a change of
these constants. Flowgentic's own attributes live under ``flowgentic.``.

Events are log records, emitted through the Logs API while the span they
belong to is current, because OpenTelemetry is deprecating span events. The
Python Logs API lives in ``opentelemetry._logs``, which OpenTelemetry's own
GenAI utilities also import.
"""

import os
import sys
from collections.abc import Callable
from importlib.metadata import version

from opentelemetry import _logs, context, metrics, propagate, trace

# GenAI semantic conventions (development status)
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_AGENT_ID = "gen_ai.agent.id"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
EXECUTE_TOOL = "execute_tool"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_TOKEN_TYPE = "gen_ai.token.type"  # nosec B105  # an attribute name
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS = "gen_ai.usage.cache_read.input_tokens"
GEN_AI_USAGE_REASONING_OUTPUT_TOKENS = "gen_ai.usage.reasoning.output_tokens"
CHAT = "chat"
INVOKE_AGENT = "invoke_agent"
EVALUATION_RESULT = "gen_ai.evaluation.result"
EVALUATION_NAME = "gen_ai.evaluation.name"
EVALUATION_SCORE_LABEL = "gen_ai.evaluation.score.label"
EVALUATION_EXPLANATION = "gen_ai.evaluation.explanation"

# The content switch that OpenTelemetry's GenAI instrumentations read
CAPTURE_MESSAGE_CONTENT = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
_CONTENT_ON_SPANS = {"SPAN_ONLY", "SPAN_AND_EVENT"}

# General semantic conventions (stable)
ERROR_TYPE = "error.type"
SERVER_ADDRESS = "server.address"
SERVER_PORT = "server.port"

# Flowgentic's attributes, joining a span to the AsyncFlow task it submitted
TASK_UID = "flowgentic.task.uid"
TASK_BACKEND = "flowgentic.task.backend"

# Flowgentic's attributes for acceptance
ACCEPT = "accept"
ACCEPT_ATTEMPT = "accept.attempt"
ACCEPT_NAME = "flowgentic.accept.name"
ACCEPT_ATTEMPTS_MAX = "flowgentic.accept.attempts.max"
ACCEPT_ATTEMPTS_USED = "flowgentic.accept.attempts.used"
ACCEPT_DISPOSITION = "flowgentic.accept.disposition"
ACCEPT_ATTEMPT_NUMBER = "flowgentic.accept.attempt"
ACCEPT_VALUE = "flowgentic.accept.value"

tracer = trace.get_tracer("flowgentic", version("flowgentic"))
logger = _logs.get_logger("flowgentic", version("flowgentic"))
meter = metrics.get_meter("flowgentic", version("flowgentic"))

# Bucket boundaries, in seconds, that each histogram advises the SDK to use. The
# SDK's default boundaries (0, 5, 10, 25, ... 10000) suit milliseconds: values
# in seconds would nearly all fall in its first bucket. A view the application
# configures overrides the advice.

LOOP_LAG_BUCKETS = (
	0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10,
)  # fmt: skip
"""1 ms to 10 s: an idle loop's lag is under a millisecond, a blocked one's
seconds."""
GC_PAUSE_BUCKETS = (
	0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1,
	0.25, 0.5, 1, 2.5, 5, 10,
)  # fmt: skip
"""100 µs to 10 s: from a young generation's pause to a full collection of a
large heap."""
TOOL_DURATION_BUCKETS = (
	0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30,
	60, 120, 300, 600, 1800, 3600, 7200,
)  # fmt: skip
"""1 ms to 2 h: from a lookup in the tool service to a long simulation task."""
ADMISSION_WAIT_BUCKETS = (
	0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30,
	60, 120, 300, 600,
)  # fmt: skip
"""1 ms to 10 min: from a request admitted at once to one that waits out a
saturated fleet or a replica's outage."""
REQUEST_DURATION_BUCKETS = (
	0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48,
	40.96, 81.92, 163.84, 327.68, 655.36,
)  # fmt: skip
"""10 ms to 11 min: the GenAI conventions' boundaries for a model call's
duration, doubled three more times for long reasoning replies."""
CALL_DURATION_BUCKETS = (*REQUEST_DURATION_BUCKETS, 1310.72, 2621.44)
"""10 ms to 44 min: a request's boundaries, doubled twice more for a call that
waits for admission and out an outage too."""
OUTAGE_DURATION_BUCKETS = (
	0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200,
)  # fmt: skip
"""100 ms to 2 h: from a server that answers its first check to a model job
requeued in a busy queue, past ``Recovery``'s default 30 min."""

# --- The agent host's metrics and events -----------------------------------------
#
# Flowgentic's own names, under ``flowgentic.``: the GenAI conventions have no
# names for agents' states or an agent process's health. Counts of agents are
# read from the hosts' AgentStates when the SDK collects, so a state change
# costs nothing extra. Without an SDK, every instrument is a no-op.

AGENT_STATE = "flowgentic.agent.state"
AGENT_CATEGORY = "flowgentic.agent.category"
GC_GENERATION = "flowgentic.gc.generation"
SUSPEND = "flowgentic.agent.suspend"
SUSPEND_WAITS = "flowgentic.agent.suspend.waits"

LOOP_LAG = meter.create_histogram(
	"flowgentic.host.loop.lag",
	unit="s",
	description="How late the agent host's event loop woke a 100 ms timer",
	explicit_bucket_boundaries_advisory=LOOP_LAG_BUCKETS,
)
GC_PAUSE = meter.create_histogram(
	"flowgentic.host.gc.pause",
	unit="s",
	description="Pauses of the cyclic garbage collector in an agent host",
	explicit_bucket_boundaries_advisory=GC_PAUSE_BUCKETS,
)


def _observe_agents(options: object) -> list:
	from flowgentic import _agents
	from flowgentic._agents import AgentState, _all_states

	if not _agents._process_has_states:
		return []
	# Every state, at 0 when no run is live, so a state's last count does not
	# linger once its agents are gone.
	totals = [0] * len(AgentState)
	for states in list(_all_states):
		counts = states._live_counts()
		if counts is not None:
			totals = [a + b for a, b in zip(totals, counts, strict=True)]
	return [
		metrics.Observation(count, {AGENT_STATE: AgentState(s).name.lower()})
		for s, count in enumerate(totals)
	]


def _observe_agent_seconds(options: object) -> list:
	from flowgentic._agents import CATEGORY, process_agent_seconds

	return [
		metrics.Observation(
			seconds,
			{
				AGENT_STATE: state.name.lower(),
				AGENT_CATEGORY: CATEGORY.get(state, "none"),
			},
		)
		for state, seconds in process_agent_seconds().items()
	]


meter.create_observable_gauge(
	"flowgentic.agents",
	callbacks=[_observe_agents],
	unit="{agent}",
	description="Agents in each state, across the process's running agent hosts",
)
meter.create_observable_counter(
	"flowgentic.agent.time",
	callbacks=[_observe_agent_seconds],
	unit="s",
	description="Agent-seconds spent in each state, by every agent the process ran",
)


def emit_suspend(agent_id: str, waits: int) -> None:
	"""Record, on the agent's span, that it suspended on ``waits`` tasks."""
	logger.emit(
		event_name=SUSPEND, attributes={GEN_AI_AGENT_ID: agent_id, SUSPEND_WAITS: waits}
	)


# --- Tools' metrics ------------------------------------------------------------
#
# A task or service tool's call is an ``execute_tool`` span, as the GenAI
# conventions name it. Where the call ran, and how it ended, are Flowgentic's:
# the conventions have no names for them.

TOOL_KIND = "flowgentic.tool.kind"
TOOL_OUTCOME = "flowgentic.tool.outcome"

TOOL_DURATION = meter.create_histogram(
	"flowgentic.tool.duration",
	unit="s",
	description="Time from a tool call's submission to its result",
	explicit_bucket_boundaries_advisory=TOOL_DURATION_BUCKETS,
)
# A task whose worker was lost runs again under its call's same span and
# duration; each run again is counted here, which is rare, so it costs nothing
# on a task that runs once.
TOOL_RETRIES = meter.create_counter(
	"flowgentic.tool.retries",
	unit="{task}",
	description="Tasks run again after their worker was lost",
)


# --- The fleet's metrics ----------------------------------------------------------
#
# Admission and the replicas' load. The loads are read from the process's fleets
# when the SDK collects, and only if ``flowgentic.fleet`` was imported.

FLEET_NAME = "flowgentic.fleet.name"
FLEET_REPLICA = "flowgentic.fleet.replica"

ADMISSION_WAIT = meter.create_histogram(
	"flowgentic.fleet.admission.wait",
	unit="s",
	description="Time a model request waited for its replica's admission",
	explicit_bucket_boundaries_advisory=ADMISSION_WAIT_BUCKETS,
)
MODEL_SERVICE = meter.create_histogram(
	"flowgentic.fleet.request.duration",
	unit="s",
	description="Time a model request spent at its replica, after admission",
	explicit_bucket_boundaries_advisory=REQUEST_DURATION_BUCKETS,
)

# A model call through the fleet is one ``chat`` span and one call duration,
# however many requests it took. The requests sent again, and the outages
# ``Recovery`` waited out, are counted where they happen, which is rare, so
# they cost nothing on a call that succeeds at once.

FLEET_ADMISSION_WAIT = "flowgentic.fleet.admission_wait"
FLEET_RECOVERY_WAIT = "flowgentic.fleet.recovery_wait"
FLEET_RESEND_COUNT = "flowgentic.fleet.resend_count"
FLEET_GAP = "flowgentic.fleet.gap"
FLEET_AFTER_TOOL = "flowgentic.fleet.after_tool"
OUTAGE_OUTCOME = "flowgentic.fleet.outage.outcome"

CALL_DURATION = meter.create_histogram(
	"flowgentic.fleet.call.duration",
	unit="s",
	description=(
		"Time a model call took through the fleet: the wait for admission, the "
		"replica's time, and any outage it waited out"
	),
	explicit_bucket_boundaries_advisory=CALL_DURATION_BUCKETS,
)
RETRIES = meter.create_counter(
	"flowgentic.fleet.retries",
	unit="{request}",
	description="Model requests sent again once their replica came back",
)
OUTAGE_DURATION = meter.create_histogram(
	"flowgentic.fleet.outage.duration",
	unit="s",
	description=(
		"How long a replica was down, from the call that found it down to its "
		"return or Recovery giving up"
	),
	explicit_bucket_boundaries_advisory=OUTAGE_DURATION_BUCKETS,
)


def _observe_fleet(kind: str) -> Callable[[object], list]:
	"""A callback that reads one kind of each replica's load: ``tokens``,
	``requests`` or ``waiting``."""

	def observe(options: object) -> list:
		fleet = sys.modules.get("flowgentic.fleet")
		if fleet is None:
			return []
		return [
			metrics.Observation(value, {FLEET_NAME: name, FLEET_REPLICA: replica})
			for (name, replica), value in fleet._loads(kind).items()
		]

	return observe


meter.create_observable_gauge(
	"flowgentic.fleet.tokens.active",
	callbacks=[_observe_fleet("tokens")],
	unit="{token}",
	description="Tokens of live context in flight at each replica, as admitted",
)
meter.create_observable_gauge(
	"flowgentic.fleet.requests.active",
	callbacks=[_observe_fleet("requests")],
	unit="{request}",
	description="Requests in flight at each replica",
)
meter.create_observable_gauge(
	"flowgentic.fleet.requests.waiting",
	callbacks=[_observe_fleet("waiting")],
	unit="{request}",
	description="Requests waiting for each replica's admission",
)


def _observe_tokens(options: object) -> list:
	"""The tokens the servers counted for the process's fleets' calls, per
	fleet name and replica; kept after a fleet is gone, so the counter only
	grows."""
	fleet = sys.modules.get("flowgentic.fleet")
	if fleet is None:
		return []
	out = []
	for (name, replica), (prompt, completion) in list(fleet._tokens.items()):
		attrs = {FLEET_NAME: name, FLEET_REPLICA: replica}
		out.append(metrics.Observation(prompt, {**attrs, GEN_AI_TOKEN_TYPE: "input"}))
		out.append(
			metrics.Observation(completion, {**attrs, GEN_AI_TOKEN_TYPE: "output"})
		)
	return out


meter.create_observable_counter(
	"flowgentic.fleet.tokens",
	callbacks=[_observe_tokens],
	unit="{token}",
	description=(
		"Prompt (input) and completion (output) tokens the servers counted for the "
		"fleets' calls, at each replica"
	),
)


def content_on_spans() -> bool:
	"""Whether the content switch asks for content on spans.

	It does for ``SPAN_ONLY`` and ``SPAN_AND_EVENT``, in any case, as the GenAI
	instrumentations read it. Read at each call, so a run can change it.
	"""
	mode = os.environ.get(CAPTURE_MESSAGE_CONTENT, "").strip().upper()
	return mode in _CONTENT_ON_SPANS


def inject_context() -> dict[str, str]:
	"""Return the current trace context as a W3C carrier, a picklable dict."""
	carrier: dict[str, str] = {}
	propagate.inject(carrier)
	return carrier


def attach_context(carrier: dict[str, str]) -> object:
	"""Make the carrier's trace context current; return the token to detach."""
	return context.attach(propagate.extract(carrier))


def detach_context(token: object) -> None:
	"""Restore the trace context that was current before ``attach_context``."""
	context.detach(token)


def record_reply(span: trace.Span, reply: object) -> None:
	"""Record a model's ``Reply`` on its ``chat`` span: the response's id and
	model, its finish reason, and the usage the server reported. A count the
	server did not report is left out, and so is a cached or reasoning count of
	zero, as the GenAI conventions do."""
	if not span.is_recording():
		return
	if reply.id:
		span.set_attribute(GEN_AI_RESPONSE_ID, reply.id)
	if reply.model:
		span.set_attribute(GEN_AI_RESPONSE_MODEL, reply.model)
	if reply.finish_reason:
		span.set_attribute(GEN_AI_RESPONSE_FINISH_REASONS, [reply.finish_reason])
	usage = reply.usage
	if usage is None:
		return
	span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, usage.prompt_tokens)
	span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, usage.completion_tokens)
	if usage.cached_tokens:
		span.set_attribute(GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS, usage.cached_tokens)
	if usage.reasoning_tokens:
		span.set_attribute(GEN_AI_USAGE_REASONING_OUTPUT_TOKENS, usage.reasoning_tokens)
