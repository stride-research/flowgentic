from typing import Optional, Any, Dict

from flowgentic.utils.telemetry.introspection import GraphIntrospector

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
from typing import (
	Annotated,
	Any,
	Callable,
	Dict,
	List,
	Optional,
	Sequence,
	Tuple,
	Literal,
)

from langchain_core.tools import BaseTool, tool
from radical.asyncflow import WorkflowEngine
from radical.asyncflow.workflow_manager import BaseExecutionBackend

from flowgentic.langGraph.memory import (
	MemoryManager, 
	MemoryConfig, 
	MemoryEnabledState,
	LangraphMemoryManager
)
import logging

from radical.asyncflow.workflow_manager import BaseExecutionBackend, WorkflowEngine
from flowgentic.langGraph.execution_wrappers import ExecutionWrappersLangraph
from flowgentic.langGraph.utils import LangraphUtils
from flowgentic.langGraph.agent_logger import AgentLogger


logger = logging.getLogger(__name__)


class LangraphIntegration:
	"""Enhanced integration between AsyncFlow WorkflowEngine and LangChain tools.

	Supports both traditional workflow patterns and React agent orchestration
	with supervisor patterns and parallel execution.
	"""

	def __init__(self, backend: BaseExecutionBackend):
		logger.info(
			f"Initializing LangGraphIntegration with backend: {type(backend).__name__}"
		)
		self.backend = backend
		self.agent_introspector = GraphIntrospector()

	async def __aenter__(self):
		logger.info("Creating WorkflowEngine for LangGraphIntegration")
		self.flow = await WorkflowEngine.create(backend=self.backend)
		self.execution_wrappers: ExecutionWrappersLangraph = ExecutionWrappersLangraph(
			flow=self.flow, instrospector=self.agent_introspector
		)
		self.utils: LangraphUtils = LangraphUtils()
		self.agent_logger: AgentLogger = AgentLogger()
		self.memory_manager: LangraphMemoryManager = LangraphMemoryManager()

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
