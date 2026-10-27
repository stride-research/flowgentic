from typing import Any, Dict, Optional

"""
LangGraph/AsyncFlow Integration: bridge AsyncFlow tasks and LangChain tools.

Key features:
- Define AsyncFlow tasks with `@flow.function_task`
- Expose as LangChain tools via `@integration.asyncflow_tool(...)`
"""

import asyncio
import contextlib
import json
import logging
import os
import random
import uuid
from abc import abstractmethod
from fileinput import filename
from functools import wraps
from typing import (
	Annotated,
	Any,
	Callable,
	Dict,
	List,
	Literal,
	Optional,
	Sequence,
	Tuple,
)

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from flowgentic import _asyncflow
from flowgentic.langGraph.execution_wrappers import ExecutionWrappersLangraph

logger = logging.getLogger(__name__)


class LangraphIntegration:
	"""Enhanced integration between AsyncFlow WorkflowEngine and LangChain tools.

	Supports both traditional workflow patterns and React agent orchestration
	with supervisor patterns and parallel execution.
	"""

	def __init__(self, backend: Any):
		"""Create an integration over one or more AsyncFlow backends.

		AsyncFlow accepts either a single backend or a list of named backends.
		Using its public interface here also avoids coupling Flowgentic to backend
		base classes that differ between AsyncFlow releases.
		"""
		logger.debug(
			"Creating the LangGraph integration over %s", type(backend).__name__
		)
		self.backend = backend

	async def __aenter__(self):
		self.flow = await _asyncflow.open_engine(self.backend)
		self.execution_wrappers: ExecutionWrappersLangraph = ExecutionWrappersLangraph(
			flow=self.flow
		)

		logger.info("Started the LangGraph integration's WorkflowEngine")
		return self

	async def __aexit__(self, exc_type, exc, tb):
		if exc_type:
			logger.warning(
				"Shutting down the WorkflowEngine after %s: %s", exc_type.__name__, exc
			)
		if self.flow:
			await _asyncflow.close_engine(self.flow)
		logger.info("Shut down the LangGraph integration's WorkflowEngine")
