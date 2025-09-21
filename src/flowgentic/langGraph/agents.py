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

from fileinput import filename

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, Field
from functools import wraps
from typing import Annotated, Any, Callable, Dict, List, Optional, Sequence, Tuple

from langchain_core.tools import BaseTool, tool
from radical.asyncflow import WorkflowEngine
from radical.asyncflow.workflow_manager import BaseExecutionBackend
from .fault_tolerance import LangraphToolFaultTolerance, RetryConfig


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
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import create_react_agent, ToolNode
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

logger = logging.getLogger(__name__)

StateType = TypeVar("StateType", bound=BaseModel)

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


class LangraphAgents:
	"""Enhanced integration between AsyncFlow WorkflowEngine and LangChain tools.

	Supports both traditional workflow patterns and React agent orchestration
	with supervisor patterns and parallel execution.
	"""

	def __init__(self, flow: WorkflowEngine) -> None:
		self.flow = flow
		self.fault_tolerance_mechanism = LangraphToolFaultTolerance()
		self.react_agents: Dict[str, Any] = {}

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
			retry_cfg = retry or self.fault_tolerance_mechanism.default_cfg
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

				return await self.fault_tolerance_mechanism.retry_async(
					_call, retry_cfg, name=f.__name__
				)

			langraph_tool = tool(wrapper)
			logger.info(f"Successfully created LangChain tool for '{f.__name__}'")
			return langraph_tool

		if func is not None:
			return decorate(func)
		return decorate

	def asyncflow_node(
		self,
		func: Optional[Callable] = None,
		*,
		retry: Optional[RetryConfig] = None,
	) -> Callable:
		"""Decorator to register an async function as AsyncFlow task and LangGraph node."""

		def decorate(f: Callable) -> Callable:
			logger.info(
				f"Registering function '{f.__name__}' as AsyncFlow task and LangGraph node"
			)

			if not self.flow:
				raise RuntimeError(
					"LangGraphIntegration must be used within async context manager"
				)

			asyncflow_func = self.flow.function_task(f)
			retry_cfg = retry or self.fault_tolerance_mechanism.default_cfg
			logger.debug(
				f"Using retry config for '{f.__name__}': {retry_cfg.model_dump()}"
			)

			@wraps(f)
			async def node_wrapper(state):
				"""LangGraph node wrapper that executes the asyncflow task and returns state"""
				logger.debug(
					f"Node '{f.__name__}' called with state keys: {list(state.keys()) if isinstance(state, dict) else type(state)}"
				)

				async def _call():
					logger.debug(f"Executing AsyncFlow task for '{f.__name__}'")
					# Execute the asyncflow task - pass state as argument to the original function
					future = asyncflow_func(state)
					result = await future
					logger.debug(
						f"AsyncFlow task '{f.__name__}' completed successfully with result: {type(result)}"
					)
					return result

				try:
					# Execute with retry mechanism
					await self.fault_tolerance_mechanism.retry_async(
						_call, retry_cfg, name=f.__name__
					)
					logger.debug(f"Node '{f.__name__}' completed successfully")

					# Always return the state for LangGraph
					return state

				except Exception as e:
					logger.error(f"Error in node '{f.__name__}': {e}")
					# Return state even on error to prevent workflow failure
				return state

			logger.info(f"Successfully created LangGraph node for '{f.__name__}'")
			return node_wrapper

		if func is not None:
			return decorate(func)
		return decorate

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
