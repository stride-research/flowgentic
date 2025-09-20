"""
LangGraph/AsyncFlow Integration: bridge AsyncFlow tasks and LangChain tools
with built-in retries, backoff, and timeouts.

Key features:
- Define AsyncFlow tasks with `@flow.function_task`
- Expose as LangChain tools via `@integration.asyncflow_tool(...)`
- Sensible defaults for fault tolerance (no config required)


TBD:
	- block for executing the agents in parallel!
	-


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
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import add_messages
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field
from functools import wraps
from typing import Annotated, Any, Callable, Dict, List, Optional, Sequence, Tuple

from langchain_core.tools import BaseTool, tool
from radical.asyncflow import WorkflowEngine
from radical.asyncflow.workflow_manager import BaseExecutionBackend


"""
Enhanced LangGraph Integration supporting both traditional workflow patterns and React agents.
Provides abstractions for common multi-agent patterns and React agent orchestration.
"""

import asyncio
import json
import os
import uuid
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Callable, Dict, Any, List, Literal, TypeVar, Generic
from functools import wraps

from langchain_core.messages import BaseMessage, AIMessage, HumanMessage
from langchain_core.tools import tool
from langchain_core.language_models.base import BaseChatModel
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import create_react_agent, ToolNode
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from flowgentic import RetryConfig, BaseLLMAgentState

logger = logging.getLogger(__name__)

StateType = TypeVar("StateType", bound=BaseModel)

from flowgentic.llm_providers import ChatLLMProvider
import logging

# Configure logging for this module
logger = logging.getLogger(__name__)


# TYPES SECTION
class BaseLLMAgentState(BaseModel):
	messages: Annotated[list, add_messages]


class EnhancedMultiAgentState(MessagesState):
	"""Enhanced state for multi-agent coordination with React agents."""

	agent_results: Dict[str, Any] = {}
	current_task: str = ""
	completed_agents: List[str] = []
	execution_stats: Dict[str, Any] = {}
	target_agents: List[str] = []


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
	logger.debug(
		f"Starting retry mechanism for '{name}' with config: max_attempts={config.max_attempts}, timeout={config.timeout_sec}"
	)

	retryable_types: Tuple[type, ...] = (
		config.retryable_exceptions or _default_retryable_exceptions()
	)
	logger.debug(
		f"Retryable exception types for '{name}': {[t.__name__ for t in retryable_types]}"
	)

	last_exc: Optional[BaseException] = None
	for attempt in range(1, max(1, config.max_attempts) + 1):
		logger.debug(f"Attempt {attempt}/{config.max_attempts} for '{name}'")
		try:
			if config.timeout_sec is not None and config.timeout_sec > 0:
				logger.debug(f"Executing '{name}' with timeout {config.timeout_sec}s")
				result = await asyncio.wait_for(call(), config.timeout_sec)
			else:
				logger.debug(f"Executing '{name}' without timeout")
				result = await call()

			logger.info(f"Successfully completed '{name}' on attempt {attempt}")
			return result
		except Exception as e:  # pylint: disable=broad-except
			last_exc = e
			is_retryable = isinstance(e, retryable_types)
			is_last = attempt >= max(1, config.max_attempts)

			logger.warning(
				f"Attempt {attempt} failed for '{name}': {type(e).__name__}: {str(e)}"
			)
			logger.debug(
				f"Exception retryable: {is_retryable}, is_last_attempt: {is_last}"
			)

			if not is_retryable or is_last:
				if config.raise_on_failure:
					logger.error(
						f"Final failure for '{name}' after {attempt} attempts: {type(e).__name__}: {str(e)}"
					)
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
				logger.error(f"Returning error payload for '{name}': {error_payload}")
				return error_payload

			# Compute backoff with jitter
			backoff = min(
				config.max_backoff_sec, config.base_backoff_sec * (2 ** (attempt - 1))
			)
			if config.jitter:
				# jitter within +/- jitter * backoff
				delta = backoff * config.jitter
				backoff = max(0.0, backoff + random.uniform(-delta, delta))

			logger.info(
				f"Retrying '{name}' in {backoff:.2f}s (attempt {attempt + 1}/{config.max_attempts})"
			)
			await asyncio.sleep(backoff)

	# Defensive: if the loop ends unexpectedly, raise last_exc if present
	if last_exc:
		logger.error(
			f"Unexpected loop termination for '{name}', raising last exception: {type(last_exc).__name__}"
		)
		raise last_exc
	return None


class ReactAgentConfig(BaseModel):
	"""Configuration for a React agent."""

	name: str
	system_prompt: str
	tools: List[Callable]
	llm: BaseChatModel
	temperature: float = 0.1


class SupervisorConfig(BaseModel):
	"""Configuration for supervisor-style orchestration."""

	routing_llm: BaseChatModel
	routing_prompt: str = "You are a supervisor routing requests to specialized agents."
	synthesizer_llm: Optional[BaseChatModel] = None
	enable_parallel_execution: bool = True


class LangGraphIntegration:
	"""Enhanced integration between AsyncFlow WorkflowEngine and LangChain tools.

	Supports both traditional workflow patterns and React agent orchestration
	with supervisor patterns and parallel execution.
	"""

	def __init__(
		self, backend: BaseExecutionBackend, default_retry: Optional[RetryConfig] = None
	):
		logger.info(
			f"Initializing LangGraphIntegration with backend: {type(backend).__name__}"
		)
		self.backend = backend
		self.default_retry = default_retry or RetryConfig()
		self.flow: Optional[WorkflowEngine] = None
		self.react_agents: Dict[str, Any] = {}
		logger.debug(f"Default retry config: {self.default_retry.model_dump()}")

	async def __aenter__(self):
		logger.info("Creating WorkflowEngine for LangGraphIntegration")
		self.flow = await WorkflowEngine.create(backend=self.backend)
		logger.info("WorkflowEngine created successfully")
		return self

	async def __aexit__(self, exc_type, exc, tb):
		logger.info("Shutting down WorkflowEngine")
		if exc_type:
			logger.warning(
				f"Exception occurred during context manager: {exc_type.__name__}: {exc}"
			)
		if self.flow:
			await self.flow.shutdown()
		logger.info("WorkflowEngine shutdown complete")

	# TOOLS SECTION - Your existing implementation
	def asyncflow_tool(
		self,
		func: Optional[Callable] = None,
		*,
		retry: Optional[RetryConfig] = None,
	) -> Callable:
		"""Decorator to register an async function as AsyncFlow task and LangChain tool."""

		def decorate(f: Callable) -> Callable:
			logger.info(
				f"Registering function '{f.__name__}' as AsyncFlow task and LangChain tool"
			)

			if not self.flow:
				raise RuntimeError(
					"LangGraphIntegration must be used within async context manager"
				)

			asyncflow_func = self.flow.function_task(f)
			retry_cfg = retry or self.default_retry
			logger.debug(
				f"Using retry config for '{f.__name__}': {retry_cfg.model_dump()}"
			)

			@wraps(f)
			async def wrapper(*args, **kwargs):
				logger.debug(
					f"Tool '{f.__name__}' called with args: {len(args)} positional, {list(kwargs.keys())} keyword"
				)

				async def _call():
					logger.debug(f"Executing AsyncFlow task for '{f.__name__}'")
					future = asyncflow_func(*args, **kwargs)
					result = await future
					logger.debug(
						f"AsyncFlow task '{f.__name__}' completed successfully"
					)
					return result

				return await self._retry_async(_call, retry_cfg, name=f.__name__)

			langraph_tool = tool(wrapper)
			logger.info(f"Successfully created LangChain tool for '{f.__name__}'")
			return langraph_tool

		if func is not None:
			return decorate(func)
		return decorate

	# REACT AGENT ABSTRACTIONS
	def create_react_agent(self, config: ReactAgentConfig) -> Any:
		"""Create a React agent with AsyncFlow-integrated tools."""
		if not self.flow:
			raise RuntimeError(
				"LangGraphIntegration must be used within async context manager"
			)

		logger.info(f"Creating React agent: {config.name}")

		# Create the React agent using LangGraph's native API
		agent = create_react_agent(
			model=config.llm, tools=config.tools, state_modifier=config.system_prompt
		)

		# Store for later reference
		self.react_agents[config.name] = {"agent": agent, "config": config}

		logger.info(f"✅ React agent '{config.name}' created successfully")
		return agent

	def create_parallel_react_executor(self, agent_configs: List[ReactAgentConfig]):
		"""Create AsyncFlow blocks for parallel React agent execution."""
		if not self.flow:
			raise RuntimeError(
				"LangGraphIntegration must be used within async context manager"
			)

		@self.flow.function_task
		async def execute_react_agent(
			agent_name: str, messages: List[BaseMessage]
		) -> Dict[str, Any]:
			"""Execute a single React agent as AsyncFlow task."""
			logger.info(f"[AsyncFlow Task] Executing React agent: {agent_name}")

			if agent_name not in self.react_agents:
				raise ValueError(
					f"React agent '{agent_name}' not found. Available: {list(self.react_agents.keys())}"
				)

			agent_info = self.react_agents[agent_name]
			agent = agent_info["agent"]

			# Execute the React agent
			state = {"messages": messages}
			result = await agent.ainvoke(state)

			return {
				"agent": agent_name,
				"messages": result["messages"],
				"result": result["messages"][-1].content if result["messages"] else "",
				"success": True,
			}

		@self.flow.block
		async def parallel_react_execution(
			messages: List[BaseMessage], task_context: str = ""
		) -> Dict[str, Any]:
			"""Execute multiple React agents in parallel."""
			logger.info(
				f"[AsyncFlow Block] Starting parallel React agents execution - Context: {task_context}"
			)

			# Launch all agents in parallel
			futures = {}
			for config in agent_configs:
				agent_messages = messages + [
					HumanMessage(
						content=f"Focus on your specialization. Context: {task_context}"
					)
				]
				futures[config.name] = execute_react_agent(config.name, agent_messages)

			# Wait for all agents to complete
			results = {}
			for agent_name, future in futures.items():
				try:
					results[agent_name] = await future
					logger.info(f"✅ Agent '{agent_name}' completed successfully")
				except Exception as e:
					logger.error(f"❌ Agent '{agent_name}' failed: {e}")
					results[agent_name] = {
						"agent": agent_name,
						"result": f"Agent failed: {str(e)}",
						"success": False,
					}

			return {
				"results": results,
				"execution_mode": "parallel",
				"agents_count": len(agent_configs),
				"successful_agents": [
					name for name, result in results.items() if result.get("success")
				],
			}

		return parallel_react_execution

	def create_supervisor_graph(
		self,
		agent_configs: List[ReactAgentConfig],
		supervisor_config: SupervisorConfig,
		state_class: type = MessagesState,
	) -> Any:
		"""Create a supervisor graph with Command API routing."""

		# Create React agents first
		for config in agent_configs:
			self.create_react_agent(config)

		# Agent node names
		agent_names = [config.name for config in agent_configs]
		parallel_executor = self.create_parallel_react_executor(agent_configs)

		def supervisor(state: state_class) -> None:
			"""Supervisor decides routing using Command API."""
			messages = state.get("messages", [])
			completed = getattr(state, "completed_agents", [])

			last_message = messages[-1].content.lower() if messages else ""

			logger.info(
				f"[Supervisor] Analyzing request, completed agents: {completed}"
			)

			# Decision logic for parallel execution
			if supervisor_config.enable_parallel_execution and not completed:
				# Check if request needs multiple agents
				agent_keywords = {
					config.name: config.name.split("_") for config in agent_configs
				}
				matching_agents = []

				for agent_name, keywords in agent_keywords.items():
					if any(keyword in last_message for keyword in keywords):
						matching_agents.append(agent_name)

				if len(matching_agents) > 1:  # Multiple agents needed
					logger.info(
						f"[Supervisor] Routing to parallel execution for agents: {matching_agents}"
					)
					return Command(
						goto="parallel_executor",
						update={
							"current_task": "parallel_execution",
							"target_agents": matching_agents,
						},
					)

			# Sequential routing for single agent requests
			for config in agent_configs:
				if config.name in last_message and config.name not in completed:
					logger.info(f"[Supervisor] Routing to agent: {config.name}")
					return Command(goto=config.name)

			# Default to synthesizer or end
			if completed:
				return Command(goto="synthesizer")

			logger.info("[Supervisor] No specific routing - ending")
			return Command(goto=END)

		def create_agent_node(config: ReactAgentConfig):
			"""Create a Command-compatible node for a React agent."""

			def agent_node(state: state_class) -> Command[Literal["supervisor"]]:
				logger.info(f"[{config.name}] Processing request...")

				agent_info = self.react_agents[config.name]
				agent = agent_info["agent"]

				# Execute React agent
				messages = state.get("messages", [])
				result = agent.invoke({"messages": messages})

				# Update completed agents
				completed = list(getattr(state, "completed_agents", []))
				if config.name not in completed:
					completed.append(config.name)

				return Command(
					goto="supervisor",
					update={
						"messages": result["messages"][-1:],
						"completed_agents": completed,
						"agent_results": {
							**getattr(state, "agent_results", {}),
							config.name: result,
						},
					},
				)

			return agent_node

		async def parallel_executor_node(
			state: state_class,
		) -> Command[Literal["synthesizer"]]:
			"""Execute React agents in parallel."""
			logger.info("[Parallel Executor] Running React agents in parallel...")

			messages = state.get("messages", [])
			task_context = getattr(state, "current_task", "")

			results = await parallel_executor(messages, task_context)

			return Command(
				goto="synthesizer",
				update={
					"agent_results": results["results"],
					"completed_agents": list(results["results"].keys()),
					"execution_stats": {
						"mode": results["execution_mode"],
						"agents_count": results["agents_count"],
						"successful_agents": results["successful_agents"],
					},
				},
			)

		def synthesizer_node(state: state_class) -> Command[Literal[END]]:
			"""Synthesize results from multiple agents."""
			logger.info("[Synthesizer] Combining agent results...")

			agent_results = getattr(state, "agent_results", {})

			if not agent_results:
				return Command(
					goto=END,
					update={
						"messages": [
							AIMessage(content="No agent results to synthesize.")
						]
					},
				)

			# Create synthesis prompt
			synthesis_content = "Based on specialist agent analysis:\n\n"
			for agent_name, result in agent_results.items():
				if isinstance(result, dict) and "result" in result:
					synthesis_content += (
						f"**{agent_name.upper()}**: {result['result']}\n\n"
					)

			synthesis_content += "Please provide a comprehensive response that integrates all this specialist knowledge."

			# Use synthesizer LLM or default to routing LLM
			synth_llm = (
				supervisor_config.synthesizer_llm or supervisor_config.routing_llm
			)
			final_response = synth_llm.invoke([HumanMessage(content=synthesis_content)])

			return Command(goto=END, update={"messages": [final_response]})

		# Build the supervisor graph
		builder = StateGraph(state_class)

		# Add supervisor
		builder.add_node("supervisor", supervisor)

		# Add agent nodes
		for config in agent_configs:
			builder.add_node(config.name, create_agent_node(config))

		# Add parallel executor and synthesizer
		builder.add_node("parallel_executor", parallel_executor_node)
		builder.add_node("synthesizer", synthesizer_node)

		# Set entry point
		builder.add_edge(START, "supervisor")

		# Compile with checkpointing
		compiled_graph = builder.compile(checkpointer=MemorySaver())

		logger.info("✅ Supervisor graph created with Command API routing")
		return compiled_graph

	# UTILITY METHODS - Your existing implementations
	async def render_graph(self, app, file_name: str = None):
		"""Render graph visualization."""
		if file_name is None:
			file_name = f"workflow_graph_{uuid.uuid4()}.png"

		dir_name = "agent_run_data"
		if not os.path.exists(dir_name):
			os.makedirs(dir_name)
		file_path = f"{dir_name}/{file_name}"

		logger.info(f"Rendering graph to file: {file_name}")
		try:
			await asyncio.to_thread(app.get_graph().draw_png, file_path)
			logger.info(f"Graph successfully rendered to {file_name}")
		except Exception as e:
			logger.error(
				f"Failed to render graph to {file_name}: {type(e).__name__}: {str(e)}"
			)
			raise

	@staticmethod
	async def needs_tool_invokation(state) -> str:
		"""Check if tool invocation is needed."""
		messages = state.get("messages", [])
		if not messages:
			return "false"

		last_message = messages[-1]
		has_tool_calls = hasattr(last_message, "tool_calls") and last_message.tool_calls

		logger.debug(
			f"Last message type: {type(last_message).__name__}, has tool calls: {has_tool_calls}"
		)
		return "true" if has_tool_calls else "false"

	def structured_final_response(
		self, llm: BaseChatModel, response_schema: BaseModel, graph_state_schema: type
	):
		"""Create structured response formatter."""
		logger.info(
			f"Creating structured response formatter with schema: {response_schema.__name__}"
		)
		formatter_llm = llm.with_structured_output(response_schema)

		async def response_structurer(current_graph_state):
			logger.debug(
				f"Structuring response for {len(current_graph_state.messages)} messages"
			)
			try:
				messages = current_graph_state.get("messages", [])
				result = await formatter_llm.ainvoke(messages)
				payload = result.model_dump()
				logger.debug(f"Successfully structured response: {payload}")
				return graph_state_schema(
					messages=[AIMessage(content=json.dumps(payload))]
				)
			except Exception as e:
				logger.error(
					f"Failed to structure response: {type(e).__name__}: {str(e)}"
				)
				raise

		return response_structurer

	async def _retry_async(self, func: Callable, retry_config: RetryConfig, name: str):
		"""Internal retry implementation."""
		# Implement your retry logic here based on retry_config
		# This is a placeholder - implement according to your RetryConfig
		try:
			return await func()
		except Exception as e:
			logger.error(f"Failed to execute {name}: {e}")
			raise


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
