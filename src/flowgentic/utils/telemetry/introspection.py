import asyncio
import inspect
import json
import sys
import time
from datetime import datetime
from copy import deepcopy
from typing import List, Dict, Any, Optional, Callable
from langgraph.types import Command
from pydantic import BaseModel, Field

from flowgentic.utils.telemetry.extractor import Extractor
from .schemas import (
	MessageInfo,
	NodeExecutionRecord,
	GraphExecutionReport,
	ToolExecutionInfo,
)
from .report_generator import ReportGenerator
import logging

logger = logging.getLogger(__name__)

# --- Core Introspection Logic ---


class GraphIntrospector:
	"""
	A class to instrument and report on a LangGraph workflow execution.

	It provides a decorator (`introspect_node`) that wraps each graph node
	to record execution details seamlessly.
	"""

	def __init__(self):
		self._start_time = datetime.now()
		self._records: Dict[str, NodeExecutionRecord] = {}
		self._final_state: Optional[BaseModel[str, Any]] = None
		self._all_nodes: List[str] = None
		self.extractor = Extractor()

	def _store_records(self, node_name_detailed: str, record):
		self._records[node_name_detailed] = record

	def record_node_event(
		self,
		node_name,
		state_before,
		state_after,
		total_messages_before,
		start_time,
		end_time,
		node_func,
	):
		node_name_detailed, record = self.extractor._final_state_extraction(
			node_name=node_name,
			state_before=state_before,
			state_after=state_after,
			total_messages_before=total_messages_before,
			start_time=start_time,
			end_time=end_time,
			node_func=node_func,
		)
		self._store_records(node_name_detailed, record)

	def introspect_node(self, node_func: Callable, node_name: str) -> Callable:
		"""
		Decorator to wrap a LangGraph node function for introspection.

		This decorator times the node's execution, captures its inputs and outputs,
		and extracts metadata like tool calls and model reasoning from the state.
		"""

		async def wrapper(state) -> Any:
			start_time = datetime.now()
			# Deepcopy to get a snapshot of the state before the node runs
			state_before = deepcopy(state)

			# Count messages before execution - handle Pydantic model
			if hasattr(state_before, "messages"):
				messages_before = state_before.messages
			else:
				messages_before = []
			total_messages_before = (
				len(messages_before) if isinstance(messages_before, list) else 0
			)

			# Execute the original node function
			state_after = await node_func(state)
			logger.debug(f"Reasoning after: {state_after}")

			end_time = datetime.now()
			self.record_node_event(
				node_name=node_name,
				state_before=state_before,
				state_after=state_after,
				total_messages_before=total_messages_before,
				start_time=start_time,
				end_time=end_time,
				node_func=node_func,
			)

			return state_after

		return wrapper

	def generate_report(self, dir_to_write: str) -> None:
		"""Generates a human-readable Markdown report of the entire graph execution."""
		if not self._all_nodes:
			raise ValueError(
				"You need to provide the the nodes for the graph to the inspector"
			)
		else:
			logger.debug(f"Records are: {self._records}")
			report_generator = ReportGenerator(
				final_state=self._final_state,
				records=self._records,
				start_time=self._start_time,
			).generate_report(all_nodes=self._all_nodes, dir_to_write=dir_to_write)
