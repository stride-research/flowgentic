from typing import Any, Dict, Optional

from flowgentic.utils.telemetry.introspection import GraphIntrospector
from flowgentic.utils.telemetry.report_generator import REPORT_FILE

"""
LangGraph/AsyncFlow Integration: bridge AsyncFlow tasks and LangChain tools
with built-in retries, backoff, and timeouts.

Key features:
- Define AsyncFlow tasks with `@flow.function_task`
- Expose as LangChain tools via `@integration.asyncflow_tool(...)`
- Sensible defaults for fault tolerance (no config required)
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
from pathlib import Path
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
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field
from radical.asyncflow import WorkflowEngine

from flowgentic.langGraph.execution_wrappers import ExecutionWrappersLangraph
from flowgentic.langGraph.memory import (
	LangraphMemoryManager,
	MemoryConfig,
	MemoryEnabledState,
	MemoryManager,
)
from flowgentic.langGraph.utils import LangraphUtils

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
		self.agent_introspector = GraphIntrospector()

	async def __aenter__(self):
		self.flow = await WorkflowEngine.create(backend=self.backend)
		self.execution_wrappers: ExecutionWrappersLangraph = ExecutionWrappersLangraph(
			flow=self.flow, instrospector=self.agent_introspector
		)
		self.utils: LangraphUtils = LangraphUtils()
		self.memory_manager: LangraphMemoryManager = LangraphMemoryManager()

		logger.info("Started the LangGraph integration's WorkflowEngine")
		return self

	async def __aexit__(self, exc_type, exc, tb):
		if exc_type:
			logger.warning(
				"Shutting down the WorkflowEngine after %s: %s", exc_type.__name__, exc
			)
		if self.flow:
			await self.flow.shutdown()
		logger.info("Shut down the LangGraph integration's WorkflowEngine")

	async def generate_execution_artifacts(
		self,
		app: CompiledStateGraph,
		caller_file_path: str,
		final_state: Dict or BaseModel,
		generate_report: bool = True,
		output_dir: str | Path | None = None,
	) -> None:
		"""
		Facade method to generate all execution artifacts (directories, reports, and graph).

		Args:
		    app: The compiled LangGraph StateGraph
		    caller_file_path: The __file__ path from the calling script
		    final_state: The graph's final state, recorded in the report
		    generate_report: Whether to write the execution report
		    output_dir: The directory for the report and the graph image.
		        Defaults to ``agent_execution_results`` beside ``caller_file_path``.

		Example:
		    await agents_manager.generate_execution_artifacts(app, __file__)
		"""
		self.agent_introspector._final_state = final_state

		if output_dir is None:
			output_dir = Path(caller_file_path).parent / "agent_execution_results"
		output_dir = Path(output_dir).resolve()
		output_dir.mkdir(parents=True, exist_ok=True)

		if generate_report:
			self.agent_introspector.generate_report(dir_to_write=str(output_dir))
		else:
			# An earlier run's report must not stay under this run's graph
			(output_dir / REPORT_FILE).unlink(missing_ok=True)

		await self.utils.render_graph(app, dir_to_write=str(output_dir))
