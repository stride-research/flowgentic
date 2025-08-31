"""
LangGraph/AsyncFlow Integration: bridge AsyncFlow tasks and LangChain tools
with built-in retries, backoff, and timeouts.

Key features:
- Define AsyncFlow tasks with `@flow.function_task`
- Expose as LangChain tools via `@integration.asyncflow_tool(...)` or
  `integration.to_langgraph_tool(task, ...)`
- Sensible defaults for fault tolerance (no config required)
"""

import asyncio
import contextlib
import random
from dataclasses import dataclass
from functools import wraps
from typing import Callable, Any, Optional, Tuple, Sequence

from langchain_core.tools import tool
from radical.asyncflow import WorkflowEngine


@dataclass
class RetryConfig:
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

    max_attempts: int = 3
    base_backoff_sec: float = 0.5
    max_backoff_sec: float = 8.0
    jitter: float = 0.25
    timeout_sec: Optional[float] = 30.0
    retryable_exceptions: Tuple[type, ...] = ()
    raise_on_failure: bool = True


def _default_retryable_exceptions() -> Tuple[type, ...]:
    """Build a tuple of retryable exception types available in this runtime.

    Keeps imports optional to avoid hard dependencies.
    """
    ex: list[type] = [asyncio.TimeoutError, TimeoutError, ConnectionError, OSError]
    # Try to include httpx timeouts if present
    try:
        import httpx  # type: ignore

        ex.extend([
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.WriteError,
            httpx.RemoteProtocolError,
        ])
    except Exception:
        pass
    # Try to include aiohttp timeouts if present
    try:
        import aiohttp  # type: ignore

        ex.extend([
            aiohttp.ClientConnectionError,
            aiohttp.ServerTimeoutError,
        ])
    except Exception:
        pass
    return tuple(set(ex))


async def _retry_async(call: Callable[[], Any], config: RetryConfig, name: str) -> Any:
    """Retry a no-arg async call with exponential backoff + jitter and timeout.

    Args:
        call: An async function with no args that performs the operation.
        config: Retry configuration.
        name: Identifier for logging/telemetry context.
    """
    retryable_types: Tuple[type, ...] = config.retryable_exceptions or _default_retryable_exceptions()

    last_exc: Optional[BaseException] = None
    for attempt in range(1, max(1, config.max_attempts) + 1):
        try:
            if config.timeout_sec is not None and config.timeout_sec > 0:
                async with asyncio.timeout(config.timeout_sec):
                    return await call()
            else:
                return await call()
        except Exception as e:  # pylint: disable=broad-except
            last_exc = e
            is_retryable = isinstance(e, retryable_types)
            is_last = attempt >= max(1, config.max_attempts)
            if not is_retryable or is_last:
                if config.raise_on_failure:
                    raise
                # Structured error payload for callers that prefer to continue
                return {
                    "tool": name,
                    "status": "error",
                    "attempts": attempt,
                    "retryable": bool(is_retryable),
                    "error_type": type(e).__name__,
                    "error": str(e),
                }

            # Compute backoff with jitter
            backoff = min(config.max_backoff_sec, config.base_backoff_sec * (2 ** (attempt - 1)))
            if config.jitter:
                # jitter within +/- jitter * backoff
                delta = backoff * config.jitter
                backoff = max(0.0, backoff + random.uniform(-delta, delta))
            await asyncio.sleep(backoff)

    # Defensive: if the loop ends unexpectedly, raise last_exc if present
    if last_exc:
        raise last_exc
    return None


class LangGraphIntegration:
    """Integration between AsyncFlow WorkflowEngine and LangChain tools.

    Provides decorator helpers that register AsyncFlow tasks and wrap them
    as LangChain tools with robust retry/backoff and timeout handling.
    """

    def __init__(self, flow: WorkflowEngine, default_retry: Optional[RetryConfig] = None):
        self.flow = flow
        self.default_retry = default_retry or RetryConfig()

    def asyncflow_tool(
        self,
        func: Optional[Callable] = None,
        *,
        retry: Optional[RetryConfig] = None,
    ) -> Callable:
        """Decorator to register an async function as AsyncFlow task and LangChain tool.

        Can be used with or without arguments:
            @integration.asyncflow_tool
            async def f(...): ...

            @integration.asyncflow_tool(retry=RetryConfig(...))
            async def f(...): ...
        """

        def decorate(f: Callable) -> Callable:
            asyncflow_func = self.flow.function_task(f)
            retry_cfg = retry or self.default_retry

            @wraps(f)
            async def wrapper(*args, **kwargs):
                async def _call():
                    future = asyncflow_func(*args, **kwargs)
                    return await future

                return await _retry_async(_call, retry_cfg, name=f.__name__)

            return tool(wrapper)

        if func is not None:
            return decorate(func)
        return decorate

    def to_langgraph_tool(self, asyncflow_task: Callable, *, retry: Optional[RetryConfig] = None) -> Callable:
        """Create a LangChain tool from an existing AsyncFlow task with retries.

        Args:
            asyncflow_task: An already decorated AsyncFlow task
            retry: Optional per-tool retry config (defaults applied if None)
        """
        retry_cfg = retry or self.default_retry

        @wraps(asyncflow_task)
        async def wrapper(*args, **kwargs):
            async def _call():
                future = asyncflow_task(*args, **kwargs)
                return await future

            return await _retry_async(_call, retry_cfg, name=asyncflow_task.__name__)

        return tool(wrapper)


# Minimal usage example (not executed):
"""
# integration = LangGraphIntegration(flow)
#
# @integration.asyncflow_tool
# async def get_weather(city: str) -> str:
#     await asyncio.sleep(0.5)
#     return f"Weather in {city} is sunny"
#
# @flow.function_task
# async def existing_task(param: str) -> str:
#     return f"Processed: {param}"
#
# tool_from_task = integration.to_langgraph_tool(existing_task)
# tools = [get_weather, tool_from_task]
"""
