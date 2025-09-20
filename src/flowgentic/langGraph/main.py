"""
LangGraph/AsyncFlow Integration: bridge AsyncFlow tasks and LangChain tools
with built-in retries, backoff, and timeouts.

Key features:
- Define AsyncFlow tasks with `@flow.function_task`
- Expose as LangChain tools via `@integration.asyncflow_tool(...)`
- Sensible defaults for fault tolerance (no config required)
"""

from abc import abstractmethod
import asyncio
import contextlib
from fileinput import filename
import json
import os
import random
import uuid
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import add_messages
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field
from functools import wraps
from typing import Annotated, Any, Callable, Dict, List, Optional, Sequence, Tuple

from langchain_core.tools import BaseTool, tool
from radical.asyncflow import WorkflowEngine
from radical.asyncflow.workflow_manager import BaseExecutionBackend

from flowgentic.llm_providers import ChatLLMProvider
import logging

logger = logging.getLogger(__name__)


# TYPES SECTION
class BaseLLMAgentState(BaseModel):
	messages: Annotated[list, add_messages]


# Fault tolerance section
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


def _default_retryable_exceptions() -> Tuple[type, ...]:
	"""Build a tuple of retryable exception types available in this runtime.

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
	except Exception:
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
	retryable_types: Tuple[type, ...] = (
		config.retryable_exceptions or _default_retryable_exceptions()
	)

	last_exc: Optional[BaseException] = None
	for attempt in range(1, max(1, config.max_attempts) + 1):
		try:
			if config.timeout_sec is not None and config.timeout_sec > 0:
				return await asyncio.wait_for(call(), config.timeout_sec)
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
			backoff = min(
				config.max_backoff_sec, config.base_backoff_sec * (2 ** (attempt - 1))
			)
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

	def __init__(
		self, backend: BaseExecutionBackend, default_retry: Optional[RetryConfig] = None
	):
		self.backend = backend
		self.default_retry = default_retry or RetryConfig()

	async def __aenter__(self):
		self.flow: WorkflowEngine = await WorkflowEngine.create(backend=self.backend)
		return self

	async def __aexit__(self, exc_type, exc, tb):
		await self.flow.shutdown()

	# TOOLS SECTION
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

			langraph_tool = tool(wrapper)
			return langraph_tool

		if func is not None:
			return decorate(func)
		return decorate

	async def render_graph(
		self,
		app: CompiledStateGraph,
		file_name: str = f"workflow_graph_{uuid.uuid4()}.png",
	):
		await asyncio.to_thread(app.get_graph().draw_png, file_name)

	@staticmethod
	async def needs_tool_invokation(state: BaseLLMAgentState) -> str:
		last_message = state.messages[-1]
		if (
			hasattr(last_message, "tool_calls") and last_message.tool_calls
		):  # Ensuring there is a tool call attr and is not empty
			return "true"
		return "false"

	# Synthetiszer node
	@staticmethod
	def structured_final_response(
		llm: BaseChatModel, response_schema: BaseModel, graph_state_schema: type
	):
		formatter_llm = llm.with_structured_output(response_schema)

		async def response_structurer(current_graph_state):
			result = await formatter_llm.ainvoke(current_graph_state.messages)
			payload = result.model_dump()
			return graph_state_schema(messages=[AIMessage(content=json.dumps(payload))])

		return response_structurer


# Minimal usage example (not executed):
"""
# integration = LangGraphIntegration(flow)
#
# @integration.asyncflow_tool
# async def get_weather(city: str) -> str:
#     await asyncio.sleep(0.5)
#     return f"Weather in {city} is sunny"
#
# tools = [get_weather]
"""
