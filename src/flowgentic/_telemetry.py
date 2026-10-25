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
from importlib.metadata import version

from opentelemetry import _logs, context, propagate, trace

# GenAI semantic conventions (development status)
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"
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
