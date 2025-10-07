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
	AGENT_TOOL_AS_MCP = "tool"  # TO BE IMPLEMENTED
	AGENT_TOOL_AS_SERVICE = "tool"  # TO BE IMPLEMENTED
	FUNCTION_TASK = "future"  # Simple asyncflow task with *args, **kwargs
	SERVICE_TASK = "future"  # TO BE IMPLEMENTED
	EXECUTION_BLOCK = "block"


class LangraphAgents:
	"""Enhanced integration between AsyncFlow WorkflowEngine and LangChain tools.

	Supports both traditional workflow patterns and React agent orchestration
	with supervisor patterns and parallel execution.
	"""

	def __init__(self, flow: WorkflowEngine) -> None:
		self.flow = flow
		self.fault_tolerance_mechanism = LangraphToolFaultTolerance()
		self.react_agents: Dict[str, Any] = {}

	def asyncflow(
		self,
		func: Optional[Callable] = None,
		*,
		flow_type: AsyncFlowType = None,
		retry: Optional[RetryConfig] = None,
	) -> Callable:
		"""
		Unified decorator to register async functions as AsyncFlow tasks with different behaviors.

		Args:
			flow_type: AsyncFlowType enum specifying the decoration behavior:
				- TOOL: Creates a LangChain tool (with @tool wrapper)
				- NODE: Creates a LangGraph node (receives state, returns state)
				- UTISL_TASK: Creates a simple asyncflow task (with *args, **kwargs)
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

				langraph_tool = tool(tool_wrapper)
				logger.info(f"Successfully created LangChain tool for '{f.__name__}'")
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
