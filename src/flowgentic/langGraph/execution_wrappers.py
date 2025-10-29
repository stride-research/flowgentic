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
from langgraph.types import Send


from langchain_core.tools import BaseTool, tool
from radical.asyncflow import WorkflowEngine
from radical.asyncflow.workflow_manager import BaseExecutionBackend
from langchain_mcp_adapters.client import MultiServerMCPClient

from flowgentic.utils.telemetry.introspection import GraphIntrospector
from .fault_tolerance import LangraphToolFaultTolerance, RetryConfig
from flowgentic.utils.llm_providers import ChatLLMProvider


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
from langgraph.prebuilt import InjectedState, create_react_agent, ToolNode
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel
from enum import Enum
from typing import Optional, Callable, Union
from functools import wraps
from langchain.tools import tool

logger = logging.getLogger(__name__)

StateType = TypeVar("StateType", bound=BaseModel)

import logging

# Configure logging for this module
logger = logging.getLogger(__name__)


# TYPES SECTION
class BaseLLMAgentState(BaseModel):
	messages: Annotated[list, add_messages]


class AsyncFlowType(Enum):
	"""Enum defining the flow_type of AsyncFlow decoration"""

	AGENT_TOOL_AS_FUNCTION = "tool"  # LangChain tool with @tool wrapper
	AGENT_TOOL_AS_MCP = "mcp_tool"  # LangChain tool representing an MCP tool
	AGENT_TOOL_AS_SERVICE = "service"  # LangChain tool with persistent service instance
	FUNCTION_TASK = "future"  # Simple asyncflow task with *args, **kwargs
	SERVICE_TASK = (
		"service_task"  # Persistent service task (service instance is cached for reuse)
	)
	EXECUTION_BLOCK = "block"


class ExecutionWrappersLangraph:
	"""Enhanced integration between AsyncFlow WorkflowEngine and LangChain tools.

	Supports both traditional workflow patterns and React agent orchestration
	with supervisor patterns and parallel execution.
	"""

	def __init__(self, flow: WorkflowEngine, instrospector: GraphIntrospector) -> None:
		self.flow = flow
		self.fault_tolerance_mechanism = LangraphToolFaultTolerance()
		self.react_agents: Dict[str, Any] = {}
		self.introspector = instrospector
		self.service_registry: Dict[str, Any] = {}

	def asyncflow(
		self,
		func: Optional[Callable] = None,
		*,
		flow_type: AsyncFlowType = None,
		retry: Optional[RetryConfig] = None,
		**kwargs,
	) -> Callable:
		"""
		Unified decorator to register async functions as AsyncFlow tasks with different behaviors.

		Args:
			flow_type: AsyncFlowType enum specifying the decoration behavior:
				- AGENT_TOOL_AS_FUNCTION: Creates a LangChain tool (with @tool wrapper)
				- AGENT_TOOL_AS_SERVICE: Creates a LangChain tool with persistent service instance
				- FUNCTION_TASK: Creates a simple asyncflow task (with *args, **kwargs)
				- SERVICE_TASK: Creates a persistent service task
				- EXECUTION_BLOCK: Creates a block node
			retry: Optional retry configuration
		"""

		def decorate(f: Callable) -> Callable:
			logger.info(
				f"Registering function '{f.__name__}' as AsyncFlow {flow_type.value}"
			)

			if not self.flow:
				raise RuntimeError(
					"LangGraphIntegration must be used within async context manager"
				)

			if flow_type == AsyncFlowType.EXECUTION_BLOCK:
				asyncflow_func = self.flow.block(f)  # Use block decorator
			elif flow_type in [
				AsyncFlowType.AGENT_TOOL_AS_SERVICE,
				AsyncFlowType.SERVICE_TASK,
			]:
				asyncflow_func = self.flow.function_task(f, service=True)
			else:
				asyncflow_func = self.flow.function_task(f)

			retry_cfg = retry or self.fault_tolerance_mechanism.default_cfg
			logger.debug(
				f"Using retry config for '{f.__name__}': {retry_cfg.model_dump()}"
			)

			if flow_type in [
				AsyncFlowType.AGENT_TOOL_AS_FUNCTION,
				AsyncFlowType.AGENT_TOOL_AS_SERVICE,
			]:
				# Tool behavior: *args, **kwargs input, with @tool wrapper
				@wraps(f)
				async def tool_wrapper(*args, **kwargs):
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

				print(
					f"passed KWARGS IS: {kwargs}, description is: {kwargs.get('tool_description')}"
				)
				langraph_tool = tool(
					tool_wrapper, description=kwargs.get("tool_description")
				)
				logger.info(f"Successfully created LangChain tool for '{f.__name__}'")
				return langraph_tool

			elif flow_type == AsyncFlowType.AGENT_TOOL_AS_MCP:
				"""
				MCP Tool: Wraps an MCP agent as a LangChain tool.
				Uses official langchain-mcp-adapters library for MCP integration.
				
				Supports two execution modes:
				- "local" (default): MCP server runs locally, agent execution on HPC
				- "remote": MCP server also runs on HPC (for compute-heavy servers)
				"""

				# Determine MCP execution mode
				mcp_mode = kwargs.get("mcp_mode", "local")
				logger.info(f"MCP execution mode for '{f.__name__}': {mcp_mode}")

				if mcp_mode == "remote":
					# Remote: MCP server runs on compute node
					mcp_servers = kwargs.get("mcp_remote_servers")
					if not mcp_servers:
						raise ValueError(
							f"MCP mode 'remote' requires 'mcp_remote_servers' parameter for '{f.__name__}'"
						)
					logger.info(f"Using remote MCP servers for '{f.__name__}'")
				elif mcp_mode == "local":
					# Local: MCP server runs on client machine (default)
					mcp_servers = kwargs.get("mcp_servers")
					if not mcp_servers:
						# Fallback to demo config if nothing provided
						logger.warning(
							f"No 'mcp_servers' provided for '{f.__name__}', using demo config"
						)
						mcp_servers = {
							"demo": {
								"command": "npx",
								"args": [
									"-y",
									"@modelcontextprotocol/server-everything",
								],
								"transport": "stdio",
							}
						}
					logger.info(f"Using local MCP servers for '{f.__name__}'")
				else:
					raise ValueError(
						f"Invalid mcp_mode '{mcp_mode}' for '{f.__name__}'. Must be 'local' or 'remote'"
					)

				# Agent cache (created once per tool)
				agent_cache = {}

				# Create the internal MCP work function
				async def internal_mcp_work(question: str) -> str:
					"""
					The actual MCP agent work that gets executed through AsyncFlow.
					This is what gets registered with self.flow.function_task()
					"""
					# Create agent once, cache it
					if "agent" not in agent_cache:
						logger.info(f"Initializing MCP agent for '{f.__name__}'")

						client = MultiServerMCPClient(mcp_servers)

						# Get all tools
						mcp_tools = await client.get_tools()
						logger.debug(
							f"Discovered {len(mcp_tools)} MCP tools for '{f.__name__}'"
						)

						# Create agent with discovered tools
						llm = kwargs.get("llm") or ChatLLMProvider(
							provider="OpenRouter", model="google/gemini-2.5-flash"
						)
						agent = create_react_agent(llm, mcp_tools)

						agent_cache["agent"] = agent
						agent_cache["client"] = client
						logger.info(f"MCP agent cached for '{f.__name__}'")

					agent = agent_cache["agent"]

					# Call the agent
					logger.debug(f"Invoking MCP agent '{f.__name__}' through AsyncFlow")
					result = await agent.ainvoke({"messages": [("user", question)]})

					# Extract response
					response = result["messages"][-1].content
					logger.debug(f"MCP agent '{f.__name__}' completed")

					return response

				# Register internal work with AsyncFlow
				asyncflow_func = self.flow.function_task(internal_mcp_work)

				@wraps(f)
				async def mcp_tool_wrapper(question: str) -> str:
					logger.debug(f"MCP Tool '{f.__name__}' called")

					async def _call():
						logger.debug(
							f"Executing MCP agent through AsyncFlow for '{f.__name__}'"
						)
						future = asyncflow_func(question)
						result = await future
						logger.debug(
							f"MCP agent '{f.__name__}' completed via AsyncFlow"
						)
						return result

					# Add retry on top
					return await self.fault_tolerance_mechanism.retry_async(
						_call, retry_cfg, name=f.__name__
					)

				langraph_tool = tool(
					mcp_tool_wrapper,
					description=kwargs.get(
						"tool_description", f.__doc__ or f"MCP agent for {f.__name__}"
					),
				)

				logger.info(f"Successfully created MCP agent tool for '{f.__name__}'")
				return langraph_tool

			elif flow_type in [AsyncFlowType.FUNCTION_TASK, AsyncFlowType.SERVICE_TASK]:

				@wraps(f)
				async def future_wrapper(*args, **kwargs):
					logger.debug(
						f"Task '{f.__name__}' called with args: {len(args)} positional, {list(kwargs.keys())} keyword"
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

				logger.info(f"Successfully created AsyncFlow task for '{f.__name__}'")
				return future_wrapper

			elif flow_type == AsyncFlowType.EXECUTION_BLOCK:

				@wraps(f)
				async def block_wrapper(state):
					"""LangGraph node: receives state, executes block, returns updated state"""
					logger.debug(f"Block '{f.__name__}' called with state")

					async def _call():
						logger.debug(f"Executing AsyncFlow block for '{f.__name__}'")
						future = asyncflow_func(state)
						result = await future
						logger.debug(
							f"AsyncFlow block '{f.__name__}' completed successfully"
						)
						return result

					return await self.fault_tolerance_mechanism.retry_async(
						_call, retry_cfg, name=f.__name__
					)

				logger.info(
					f"Successfully created LangGraph block node for '{f.__name__}'"
				)
				return block_wrapper
			else:
				raise ValueError(f"Unsupported AsyncFlow flow_type: {flow_type}")

		if func is not None:
			return decorate(func)
		return decorate

	def create_task_description_handoff_tool(self, agent_name: str, description: str):
		name = f"transfer_to_{agent_name}"
		description = description or f"Ask {agent_name} for help."

		# Execute the handoff via HPC by registering as an AsyncFlow function task
		async def _handoff_task(
			task_description: str,
			state: BaseLLMAgentState,
		) -> Command:
			self.introspector.record_supervisor_event(
				state=state,
				destination=agent_name,
				task_description=task_description,
				node_name="supervisor",
			)

			# Build a plain-mapping payload for Send. Pydantic models are not mappings.
			task_description_message = HumanMessage(content=task_description)
			base_state = state.model_dump()
			agent_input = {**base_state, "messages": [task_description_message]}

			return Command(
				goto=[Send(agent_name, agent_input)],
				graph=Command.PARENT,
			)

		asyncflow_func = self.flow.function_task(_handoff_task)

		@tool(name, description=description)
		async def handoff_tool(
			task_description: Annotated[
				str,
				"Description of what the next agent should do, including all of the relevant context.",
			],
			state: Annotated[BaseLLMAgentState, InjectedState],
		) -> Command:
			return await asyncflow_func(task_description, state)

		return handoff_tool
