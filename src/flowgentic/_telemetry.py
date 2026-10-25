"""Flowgentic's OpenTelemetry names, tracer and trace-context carrier.

Flowgentic depends on the OpenTelemetry API only: without an SDK configured by
the application, every span here is a no-op. The GenAI names follow
``open-telemetry/semantic-conventions-genai`` at commit ``9ffc7e0``
(2026-10-21), which has no release yet; a change of conventions is a change of
these constants. Flowgentic's own attributes live under ``flowgentic.``.
"""

from importlib.metadata import version

from opentelemetry import context, propagate, trace

# GenAI semantic conventions (development status)
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_AGENT_NAME = "gen_ai.agent.name"
INVOKE_AGENT = "invoke_agent"

# General semantic conventions (stable)
ERROR_TYPE = "error.type"

# Flowgentic's attributes, joining a span to the AsyncFlow task it submitted
TASK_UID = "flowgentic.task.uid"
TASK_BACKEND = "flowgentic.task.backend"

tracer = trace.get_tracer("flowgentic", version("flowgentic"))


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
