"""Accept or reject an agent's output on explicit criteria.

In an unattended campaign, nobody reads an agent's proposal before it becomes
HPC work. ``accept`` asks a producer for a value and runs the application's
checks on it, in order, cheapest first. A rejected proposal is asked for again,
within an attempt budget, with the reasons it was rejected. When no proposal
passes, a deterministic fallback keeps the campaign going, or ``NotAccepted``
stops it with every attempt attached.

```python
from flowgentic.acceptance import Rejected, accept


def within_bounds(decision: PlannerDecision) -> None:
    outside = [x for x in decision.candidates if not LOWER <= x <= UPPER]
    if outside:
        raise Rejected(f"{outside} lie outside [{LOWER}, {UPPER}]")


outcome = await accept(
    plan, checks=[within_bounds], attempts=2, fallback=surrogate_batch
)
outcome.value  # the accepted proposal, or the fallback's
outcome.disposition  # "accepted" or "fallback"
outcome.attempts  # every attempt, in order
```

A rejection is not a failure. A check, or the producer, rejects an output by
raising ``Rejected`` or a Pydantic ``ValidationError``, and ``accept`` asks
again. Any other exception is a failure: it propagates at once, with no further
attempt, and the application decides whether to fall back.

Each call is an OpenTelemetry ``accept {name}`` span with one
``accept.attempt {name}`` span per attempt. Each check that ran emits a
``gen_ai.evaluation.result`` event while its attempt's span is current. A
rejected value is recorded on its attempt's span only when
``OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`` is ``SPAN_ONLY`` or
``SPAN_AND_EVENT``; the ``Outcome`` always holds it.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from opentelemetry.trace import Span, SpanKind
from pydantic import ValidationError
from pydantic_core import to_json

from flowgentic import _telemetry

T = TypeVar("T")

Disposition = Literal["accepted", "fallback"]


class Rejected(Exception):
	"""An output that is not acceptable.

	A check raises it to reject a proposal. The producer raises it for output
	it cannot use, such as an answer that does not parse, a refusal or a
	truncated answer, and names that stage with ``check``.

	Args:
	    reason: Why the output was rejected. The next attempt receives it, so
	        say what an acceptable output needs.
	    check: The stage that rejected the output, such as ``"schema"``. When
	        omitted, the rejection is named after the check or producer that
	        raised it.
	"""

	def __init__(self, reason: str, *, check: str | None = None) -> None:
		super().__init__(reason)
		self.reason = reason
		self.check = check

	def __reduce__(self) -> tuple[Any, ...]:
		# A process pool pickles what a task raises; keep check through it
		return (type(self), (self.reason,), {"check": self.check})


@dataclass(frozen=True)
class Attempt(Generic[T]):
	"""One proposal and its verdict.

	Attributes:
	    value: The proposal, or ``None`` when the producer rejected its own
	        output.
	    rejected_by: The check or stage that rejected it, or ``None`` if it was
	        accepted.
	    reason: Why it was rejected, or ``None`` if it was accepted.
	"""

	value: T | None
	rejected_by: str | None = None
	reason: str | None = None


@dataclass(frozen=True)
class Outcome(Generic[T]):
	"""The value ``accept`` settled on, and how.

	Attributes:
	    value: The accepted proposal, or the fallback's value.
	    disposition: ``"accepted"`` or ``"fallback"``.
	    attempts: Every attempt, in order; an accepted proposal's is last.
	"""

	value: T
	disposition: Disposition
	attempts: tuple[Attempt[T], ...]


class NotAccepted(Exception):
	"""Raised when no proposal passed and there is no fallback.

	Attributes:
	    attempts: Every attempt, in order.
	"""

	def __init__(self, message: str, attempts: tuple[Attempt[Any], ...]) -> None:
		super().__init__(message)
		self.attempts = attempts

	def __reduce__(self) -> tuple[Any, ...]:
		return (type(self), (str(self), self.attempts))


async def accept(
	propose: Callable[[tuple[Attempt[T], ...]], Awaitable[T]],
	*,
	checks: Sequence[Callable[[T], object]] = (),
	attempts: int = 1,
	fallback: Callable[[], T | Awaitable[T]] | None = None,
	name: str | None = None,
) -> Outcome[T]:
	"""Ask for proposals until one passes every check, or the attempts run out.

	Args:
	    propose: Returns the next proposal. It receives every earlier attempt,
	        ``()`` on the first call, and builds the re-ask from their reasons.
	    checks: Each takes a proposal and raises ``Rejected`` to reject it, sync
	        or async. They run in order and stop at the first rejection, so put
	        the cheapest first. Normalising a value, such as rounding, belongs
	        in the producer or the schema, not in a check.
	    attempts: How many proposals to ask for. The default, 1, asks once.
	    fallback: Returns the value to use when no proposal passes, sync or
	        async. It is not checked, so it must be valid by construction.
	    name: Labels the spans; ``propose.__name__`` by default.

	Returns:
	    The accepted proposal or the fallback's value, with every attempt.

	Raises:
	    NotAccepted: No proposal passed, and there is no fallback.
	    ValueError: ``attempts`` is less than 1.
	"""
	if attempts < 1:
		raise ValueError(f"attempts must be at least 1, not {attempts}")
	name = name or _name_of(propose)
	record: list[Attempt[T]] = []
	with _telemetry.tracer.start_as_current_span(
		f"{_telemetry.ACCEPT} {name}",
		kind=SpanKind.INTERNAL,
		attributes={
			_telemetry.ACCEPT_NAME: name,
			_telemetry.ACCEPT_ATTEMPTS_MAX: attempts,
		},
	) as span:
		used = 0
		try:
			for number in range(1, attempts + 1):
				used = number
				attempt = await _attempt(propose, checks, tuple(record), number, name)
				record.append(attempt)
				if attempt.rejected_by is None:
					return _settle(span, attempt.value, "accepted", record)
			if fallback is None:
				span.set_attribute(_telemetry.ACCEPT_DISPOSITION, "not_accepted")
				last = record[-1]
				raise NotAccepted(
					f"{name}: no proposal accepted in {attempts} attempt(s); the "
					f"last was rejected by {last.rejected_by}: {last.reason}",
					tuple(record),
				)
			return _settle(span, await _resolve(fallback()), "fallback", record)
		except Exception as exc:
			span.set_attribute(_telemetry.ERROR_TYPE, type(exc).__qualname__)
			raise
		finally:
			span.set_attribute(_telemetry.ACCEPT_ATTEMPTS_USED, used)


async def _attempt(
	propose: Callable[[tuple[Attempt[T], ...]], Awaitable[T]],
	checks: Sequence[Callable[[T], object]],
	previous: tuple[Attempt[T], ...],
	number: int,
	name: str,
) -> Attempt[T]:
	"""Ask for one proposal and judge it, in the attempt's own span."""
	with _telemetry.tracer.start_as_current_span(
		f"{_telemetry.ACCEPT_ATTEMPT} {name}",
		kind=SpanKind.INTERNAL,
		attributes={_telemetry.ACCEPT_ATTEMPT_NUMBER: number},
	) as span:
		try:
			return await _judge(span, propose, checks, previous, name)
		except Exception as exc:
			span.set_attribute(_telemetry.ERROR_TYPE, type(exc).__qualname__)
			raise


async def _judge(
	span: Span,
	propose: Callable[[tuple[Attempt[T], ...]], Awaitable[T]],
	checks: Sequence[Callable[[T], object]],
	previous: tuple[Attempt[T], ...],
	name: str,
) -> Attempt[T]:
	"""Ask for a proposal, then run the checks on it until one rejects it."""
	try:
		value = await propose(previous)
	except (Rejected, ValidationError) as exc:
		return _reject(None, exc, name, None)
	response_id = _response_id(value)
	for check in checks:
		check_name = _name_of(check)
		try:
			await _resolve(check(value))
		except (Rejected, ValidationError) as exc:
			if _telemetry.content_on_spans():
				span.set_attribute(
					_telemetry.ACCEPT_VALUE, to_json(value, fallback=str).decode()
				)
			return _reject(value, exc, check_name, response_id)
		_evaluated(check_name, "pass", None, response_id)
	return Attempt(value)


def _reject(
	value: T | None,
	exc: Rejected | ValidationError,
	raised_by: str,
	response_id: str | None,
) -> Attempt[T]:
	"""Record a rejection as an attempt and as a failed evaluation."""
	if isinstance(exc, Rejected):
		rejected_by, reason = exc.check or raised_by, exc.reason
	else:
		rejected_by, reason = raised_by, str(exc)
	_evaluated(rejected_by, "fail", reason, response_id)
	return Attempt(value, rejected_by, reason)


def _evaluated(
	check: str, label: str, explanation: str | None, response_id: str | None
) -> None:
	"""Emit one ``gen_ai.evaluation.result`` event in the current span."""
	attributes = {
		_telemetry.EVALUATION_NAME: check,
		_telemetry.EVALUATION_SCORE_LABEL: label,
	}
	if explanation is not None:
		attributes[_telemetry.EVALUATION_EXPLANATION] = explanation
	if response_id is not None:
		attributes[_telemetry.GEN_AI_RESPONSE_ID] = response_id
	_telemetry.logger.emit(
		event_name=_telemetry.EVALUATION_RESULT, attributes=attributes
	)


def _settle(
	span: Span, value: T, disposition: Disposition, record: list[Attempt[T]]
) -> Outcome[T]:
	"""Record the disposition on the accept span and return the outcome."""
	span.set_attribute(_telemetry.ACCEPT_DISPOSITION, disposition)
	return Outcome(value, disposition, tuple(record))


def _response_id(value: object) -> str | None:
	"""Return the provider's response id, if the value is a message with one.

	LangChain's chat messages keep it in ``response_metadata["id"]``.
	"""
	metadata = getattr(value, "response_metadata", None)
	if isinstance(metadata, Mapping):
		response_id = metadata.get("id")
		if isinstance(response_id, str):
			return response_id
	return None


async def _resolve(result: T | Awaitable[T]) -> T:
	"""Await the result of a sync-or-async callable, if it is awaitable."""
	if inspect.isawaitable(result):
		return await result
	return result


def _name_of(function: object) -> str:
	"""Name a producer or check, for the spans and the attempts."""
	return getattr(function, "__name__", None) or type(function).__name__
