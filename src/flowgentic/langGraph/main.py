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
from langgraph.graph import add_messages, StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field
from functools import wraps
from typing import Annotated, Any, Callable, Dict, List, Optional, Sequence, Tuple, Literal

from langchain_core.tools import BaseTool, tool
from radical.asyncflow import WorkflowEngine
from radical.asyncflow.workflow_manager import BaseExecutionBackend

from flowgentic.llm_providers import ChatLLMProvider
from flowgentic.langGraph.memory import MemoryManager, MemoryConfig, MemoryEnabledState
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

	# Synthesizer node
	@staticmethod
	def structured_final_response(
		llm: BaseChatModel, response_schema: type, graph_state_schema: type
	):
		formatter_llm = llm.with_structured_output(response_schema)

		async def response_structurer(current_graph_state):
			result = await formatter_llm.ainvoke(current_graph_state.messages)
			payload = result.model_dump() if hasattr(result, 'model_dump') else dict(result)
			return graph_state_schema(messages=[AIMessage(content=json.dumps(payload))])

		return response_structurer


class MemoryEnabledLangGraphIntegration(LangGraphIntegration):
	"""Enhanced LangGraph integration with memory management capabilities.

	Extends the base integration with memory-aware operations for enhanced
	agent capabilities and conversation continuity.
	"""

	def __init__(
		self,
		backend: BaseExecutionBackend,
		memory_manager: MemoryManager,
		llm: Optional[BaseChatModel] = None,
		default_retry: Optional[RetryConfig] = None
	):
		super().__init__(backend, default_retry)
		self.memory_manager = memory_manager
		self.llm = llm

	async def invoke_with_memory(
		self,
		state: MemoryEnabledState,
		user_id: str,
		tools: List[Callable] = None
	) -> MemoryEnabledState:
		"""Enhanced invocation with memory context integration."""
		# Get relevant memory context
		last_message = state.messages[-1] if state.messages else None
		query = last_message.content if last_message and hasattr(last_message, 'content') else ""
		memory_context = await self.memory_manager.get_relevant_context(
			user_id=user_id,
			query=str(query) if query else ""

		)

		# Update state with memory context
		enhanced_state = state.model_copy(update={"memory_context": memory_context})

		# Add interaction to memory
		await self.memory_manager.add_interaction(
			user_id=user_id,
			messages=state.messages,
			metadata={"tool_calls": len(state.messages)}
		)

		return enhanced_state

	@staticmethod
	async def needs_tool_invokation_with_memory(
		state: MemoryEnabledState,
		memory_manager: MemoryManager,
		user_id: str
	) -> str:
		"""Memory-aware tool invocation decision making."""
		last_message = state.messages[-1]

		# Check if we need tools based on memory context
		if hasattr(last_message, "tool_calls") and last_message.tool_calls:
			return "true"

		# Basic fallback to standard logic
		return await LangGraphIntegration.needs_tool_invokation(
			BaseLLMAgentState(messages=state.messages)
		)

	@staticmethod
	def structured_final_response_with_memory(
		llm: BaseChatModel,
		response_schema: type,
		graph_state_schema: type,
		memory_manager: MemoryManager
	):
		"""Enhanced structured response with memory context integration."""
		formatter_llm = llm.with_structured_output(response_schema)

		async def response_structurer(current_graph_state: MemoryEnabledState):
			# Get memory context to inform response
			user_id = current_graph_state.user_id
			memory_context = await memory_manager.get_relevant_context(user_id)

			# Use memory context in response generation
			enhanced_messages = current_graph_state.messages.copy()

			# Add relevant memories to context if needed
			if memory_context.get("memory_stats", {}).get("total_messages", 0) > 0:
				context_message = f"Recent conversation: {memory_context['memory_stats']['total_messages']} messages"
				enhanced_messages.insert(0, AIMessage(content=context_message))

			result = await formatter_llm.ainvoke(enhanced_messages)
			payload = result.model_dump() if hasattr(result, 'model_dump') else result
			return graph_state_schema(messages=[AIMessage(content=json.dumps(payload))])

		return response_structurer


def create_memory_enabled_graph(
	integration: MemoryEnabledLangGraphIntegration,
	memory_manager: MemoryManager,
	tools: List[Callable],
	llm: Optional[BaseChatModel] = None
):
	"""Create a memory-enabled LangGraph workflow."""

	async def memory_enhanced_chatbot(state: MemoryEnabledState):
		"""Memory-enhanced chatbot node."""
		user_id = state.user_id

		# Add interaction to memory
		await memory_manager.add_interaction(
			user_id=user_id,
			messages=state.messages
		)

		# Use memory-enhanced LLM invocation
		enhanced_state = await integration.invoke_with_memory(state, user_id, tools)
		return enhanced_state

	async def memory_context_provider(state: MemoryEnabledState):
		"""Provide memory context for next steps."""
		user_id = state.user_id
		memory_context = await memory_manager.get_relevant_context(user_id)
		return {"memory_context": memory_context}

	# Build graph with memory nodes
	workflow = StateGraph(MemoryEnabledState)
	workflow.add_node("memory_context", memory_context_provider)
	workflow.add_node("chatbot", memory_enhanced_chatbot)
	workflow.add_node("tools", ToolNode(tools))

	workflow.add_edge(START, "memory_context")
	workflow.add_edge("memory_context", "chatbot")
	workflow.add_conditional_edges(
		"chatbot",
		lambda s: integration.needs_tool_invokation_with_memory(s, memory_manager, s.user_id),
		{"true": "tools", "false": END}
	)
	workflow.add_edge("tools", "chatbot")

	return workflow.compile()


# Minimal usage example (not executed):
"""
# Setup with memory and summarization
memory_config = MemoryConfig(
    max_short_term_messages=50,
    short_term_strategy="summarize",
    enable_summarization=True
)
memory_manager = MemoryManager(memory_config, llm=llm)  # Pass LLM for summarization

# Enhanced integration
integration = MemoryEnabledLangGraphIntegration(backend, memory_manager, llm=llm)

# Create memory-enabled graph
app = create_memory_enabled_graph(integration, memory_manager, tools, llm=llm)

# Use with user context
config = {"configurable": {"thread_id": "1", "user_id": "user123"}}
result = app.invoke(
    {"messages": [HumanMessage(content="Hello")], "user_id": "user123"},
    config=config
)
"""
