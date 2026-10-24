from pydantic import BaseModel, Field
from typing import Optional, Tuple, Callable, Any
import logging
import asyncio
import secrets
from functools import cached_property

logger = logging.getLogger(__name__)


class RetryConfig(BaseModel):
	"""Configuration for retry/backoff and timeouts.

	Attributes:
	    max_attempts: Total attempts including the first try.
	    base_backoff_sec: Base delay used for exponential backoff.
	    max_backoff_sec: Upper bound for backoff delay.
	    jitter: Randomization factor [0.0-1.0] applied to backoff.
	    timeout_sec: Per-attempt timeout (None disables timeout).
	    retryable_exceptions: Tuple of exception classes considered transient.
	    raise_on_failure: If True, raise after final failure; otherwise return an error payload.
	"""

	max_attempts: int = Field(
		default=3, description="Total attempts including the first try"
	)
	base_backoff_sec: float = Field(
		default=0.5, description="Base delay used for exponential backoff"
	)
	max_backoff_sec: float = Field(
		default=8.0, description="Upper bound for backoff delay"
	)
	jitter: float = Field(
		default=0.25, description="Randomization factor [0.0-1.0] applied to backoff"
	)
	timeout_sec: Optional[float] = Field(
		default=30.0, description="Per-attempt timeout (None disables timeout)"
	)
	retryable_exceptions: Tuple[type, ...] = Field(
		default=(), description="Tuple of exception classes considered transient"
	)
	raise_on_failure: bool = Field(
		default=True,
		description="If True, raise after final failure; otherwise return an error payload",
	)


class LangraphToolFaultTolerance:
	def __init__(self) -> None:
		self.default_cfg = RetryConfig()

	@cached_property
	def _default_retryable(self) -> Tuple[type, ...]:
		"""The default retryable exceptions, worked out once per instance."""
		return self._default_retryable_exceptions()

	def _default_retryable_exceptions(self) -> Tuple[type, ...]:
		"""Build a tuple of retryable exception types available in this runtime.

		Fallback when no explicit exception are provided by the client

		Keeps imports optional to avoid hard dependencies.
		"""
		ex: list[type] = [asyncio.TimeoutError, TimeoutError, ConnectionError, OSError]
		# Try to include httpx timeouts if present
		try:
			import httpx  # type: ignore

			ex.extend(
				[
					httpx.ConnectError,
					httpx.ReadTimeout,
					httpx.WriteError,
					httpx.RemoteProtocolError,
				]
			)
		except ImportError:
			pass
		# Try to include aiohttp timeouts if present
		try:
			import aiohttp  # type: ignore

			ex.extend(
				[
					aiohttp.ClientConnectionError,
					aiohttp.ServerTimeoutError,
				]
			)
		except ImportError:
			pass
		return tuple(set(ex))

	async def retry_async(
		self, call: Callable[[], Any], config: RetryConfig, name: str
	) -> Any:
		"""Retry a no-arg async call with exponential backoff + jitter and timeout.

		Args:
		call: An async function with no args that performs the operation.
		config: Retry configuration.
		name: Identifier for logging/telemetry context.
		"""
		logger.debug(
			"Calling '%s' with max_attempts=%d, timeout=%s",
			name,
			config.max_attempts,
			config.timeout_sec,
		)

		retryable_types: Tuple[type, ...] = (
			config.retryable_exceptions or self._default_retryable
		)
		logger.debug(
			"Retryable exception types for '%s': %s",
			name,
			[t.__name__ for t in retryable_types],
		)

		last_exc: Optional[BaseException] = None
		for attempt in range(1, max(1, config.max_attempts) + 1):
			logger.debug("Attempt %d/%d for '%s'", attempt, config.max_attempts, name)
			try:
				if config.timeout_sec is not None and config.timeout_sec > 0:
					result = await asyncio.wait_for(call(), config.timeout_sec)
				else:
					result = await call()

				logger.debug("Completed '%s' on attempt %d", name, attempt)
				return result
			except Exception as e:  # pylint: disable=broad-except
				last_exc = e
				is_retryable = isinstance(e, retryable_types)
				is_last = attempt >= max(1, config.max_attempts)

				if not is_retryable or is_last:
					logger.error(
						"'%s' failed on attempt %d/%d (%s): %s: %s",
						name,
						attempt,
						config.max_attempts,
						"retryable" if is_retryable else "not retryable",
						type(e).__name__,
						e,
					)
					if config.raise_on_failure:
						raise
					# Structured error payload for callers that prefer to continue
					error_payload = {
						"tool": name,
						"status": "error",
						"attempts": attempt,
						"retryable": bool(is_retryable),
						"error_type": type(e).__name__,
						"error": str(e),
					}
					logger.debug("Returning an error payload for '%s'", name)
					return error_payload

				# Compute backoff with jitter
				backoff = min(
					config.max_backoff_sec,
					config.base_backoff_sec * (2 ** (attempt - 1)),
				)
				if config.jitter:
					# jitter within +/- jitter * backoff
					delta = backoff * config.jitter
					backoff = max(
						0.0, backoff + secrets.SystemRandom().uniform(-delta, delta)
					)

				logger.warning(
					"Attempt %d/%d for '%s' failed (%s: %s); retrying in %.2fs",
					attempt,
					config.max_attempts,
					name,
					type(e).__name__,
					e,
					backoff,
				)
				await asyncio.sleep(backoff)

		# Defensive: if the loop ends unexpectedly, raise last_exc if present
		if last_exc:
			logger.error(
				"Retry loop for '%s' ended unexpectedly; raising %s",
				name,
				type(last_exc).__name__,
			)
			raise last_exc
		return None
