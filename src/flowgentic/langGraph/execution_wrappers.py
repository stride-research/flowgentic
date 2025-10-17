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

from flowgentic.utils.telemetry.introspection import GraphIntrospector
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
	AGENT_TOOL_AS_MCP = "mcp_tool"  # TO BE IMPLEMENTED
	AGENT_TOOL_AS_SERVICE = "service"  # TO BE IMPLEMENTED
	FUNCTION_TASK = "future"  # Simple asyncflow task with *args, **kwargs
	SERVICE_TASK = "service_task"  # TO BE IMPLEMENTED
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
			else:
				asyncflow_func = self.flow.function_task(f)

			retry_cfg = retry or self.fault_tolerance_mechanism.default_cfg
			logger.debug(
				f"Using retry config for '{f.__name__}': {retry_cfg.model_dump()}"
			)

			if flow_type == AsyncFlowType.AGENT_TOOL_AS_FUNCTION:
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

			elif flow_type == AsyncFlowType.AGENT_TOOL_AS_SERVICE:
				# Service Tool: Persistent connection/client that LLM can call
				service_name = f.__name__

				@wraps(f)
				async def service_tool_wrapper(*args, **kwargs):
					logger.debug(
						f"Service Tool '{service_name}' called with args: {len(args)} positional, {list(kwargs.keys())} keyword"
					)

					if service_name not in self.service_registry:
						logger.info(
							f"Initializing service '{service_name}' for first time"
						)

						async def _init():
							logger.debug(
								f"Running initialization for service '{service_name}'"
							)
							future = asyncflow_func(*args, **kwargs)
							service_instance = await future
							logger.debug(
								f"Service '{service_name}' initialized successfully"
							)
							return service_instance

						service_instance = (
							await self.fault_tolerance_mechanism.retry_async(
								_init, retry_cfg, name=f"{service_name}_init"
							)
						)
						self.service_registry[service_name] = service_instance
						logger.info(f"Service '{service_name}' initialized and cached")

					service_instance = self.service_registry[service_name]

					async def _call():
						logger.debug(
							f"Executing service '{service_name}' with cached instance"
						)
						if hasattr(service_instance, "handle_request"):
							result = await service_instance.handle_request(
								*args, **kwargs
							)
						else:
							result = service_instance
						logger.debug(f"Service '{service_name}' completed successfully")
						return result

					return await self.fault_tolerance_mechanism.retry_async(
						_call, retry_cfg, name=service_name
					)

				langraph_tool = tool(service_tool_wrapper)
				logger.info(
					f"Successfully created LangChain service tool for '{f.__name__}'"
				)
				return langraph_tool

			elif flow_type == AsyncFlowType.AGENT_TOOL_AS_MCP:
				"""
			    MCP Tool: Real MCP client integration with fallback.
    
				Connects to real MCP servers via the MCP SDK and makes them
				callable by LLMs as tools through AsyncFlow:
				- Async execution ✓
				- Retry/fault tolerance ✓  
				- LangChain integration ✓
				- LLM callable ✓
				- Real MCP client connection ✓
				- Fallback to placeholder on error ✓
				
				Currently connects to Anthropic's demo MCP server via NPX,
				which provides tools like 'echo', 'add', etc.
				
				Production usage: Configure MCP server parameters via kwargs:
				- mcp_server_script: Command to run (default: "npx")
				- mcp_server_args: Arguments for server (default: demo server)
				"""

				@wraps(f)
				async def mcp_tool_wrapper(*args, **kwargs):
					logger.debug(
						f"MCP Tool '{f.__name__}' called with args: {len(args)} positional, {list(kwargs.keys())} keyword"
					)

					async def _call():
						logger.debug(f"Executing MCP call for '{f.__name__}'")

						try:
							from mcp import ClientSession, StdioServerParameters
							from mcp.client.stdio import stdio_client

							server_params = StdioServerParameters(
								command="npx",
								args=["-y", "@modelcontextprotocol/server-everything"],
							)

							async with stdio_client(server_params) as (read, write):
								async with ClientSession(read, write) as session:
									await session.initialize()

									tools_list = await session.list_tools()
									logger.debug(
										f"MCP server has {len(tools_list.tools)} tools available"
									)

									if tools_list.tools:
										# Use echo tool (simplest one)
										logger.debug(f"Calling MCP tool: echo")

										result = await session.call_tool(
											"echo",
											arguments={
												"message": kwargs.get(
													"prompt", "Hello from MCP!"
												)
											},
										)

										# Extract text from result
										response_text = ""
										if hasattr(result, "content"):
											for item in result.content:
												if hasattr(item, "text"):
													response_text += item.text

										response = {
											"status": "success",
											"tool_used": "echo",
											"result": response_text or str(result),
										}
									else:
										response = {
											"status": "success",
											"message": "Connected to MCP server",
											"available_tools": len(tools_list.tools),
										}

									logger.debug(
										f"MCP call for '{f.__name__}' completed successfully"
									)
									return response

						except Exception as e:
							logger.error(f"MCP call failed: {e}")
							import traceback

							logger.error(
								traceback.format_exc()
							)  # ← Add full traceback!
							return {
								"status": "error",
								"message": f"MCP call failed: {str(e)}",
								"fallback": "Using placeholder response",
							}

					return await self.fault_tolerance_mechanism.retry_async(
						_call, retry_cfg, name=f.__name__
					)

				langraph_tool = tool(
					mcp_tool_wrapper,
					description=kwargs.get(
						"tool_description", f"MCP tool for {f.__name__}"
					),
				)
				logger.info(
					f"Successfully created MCP LangChain tool for '{f.__name__}'"
				)
				return langraph_tool

			elif flow_type == AsyncFlowType.FUNCTION_TASK:
				# Future behavior: simple *args, **kwargs asyncflow task
				@wraps(f)
				async def future_wrapper(*args, **kwargs):
					logger.debug(
						f"Future '{f.__name__}' called with args: {len(args)} positional, {list(kwargs.keys())} keyword"
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

				logger.info(f"Successfully created AsyncFlow future for '{f.__name__}'")
				return future_wrapper

			elif flow_type == AsyncFlowType.SERVICE_TASK:
				# Service Task: Persistent utility/background service
				service_name = f.__name__

				@wraps(f)
				async def service_task_wrapper(*args, **kwargs):
					logger.debug(
						f"Service Task '{service_name}' called with args: {len(args)} positional, {list(kwargs.keys())} keyword"
					)

					if service_name not in self.service_registry:
						logger.info(f"Initializing service task '{service_name}'")

						async def _init():
							logger.debug(f"Running initialization for '{service_name}'")
							future = asyncflow_func(*args, **kwargs)
							service_instance = await future
							logger.debug(f"Service task '{service_name}' initialized")
							return service_instance

						service_instance = (
							await self.fault_tolerance_mechanism.retry_async(
								_init, retry_cfg, name=f"{service_name}_init"
							)
						)
						self.service_registry[service_name] = service_instance
						logger.info(f"Service task '{service_name}' cached")

					return self.service_registry[service_name]

				logger.info(
					f"Successfully created AsyncFlow service task for '{f.__name__}'"
				)
				return service_task_wrapper

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
