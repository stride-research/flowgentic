import asyncio
import inspect
import json
import sys
import time
from datetime import datetime
from copy import deepcopy
from typing import List, Dict, Any, Optional, Callable
from langchain_core.messages import (
	AIMessage,
	BaseMessage,
	HumanMessage,
	SystemMessage,
	ToolMessage,
)
from langgraph.types import Command
from pydantic import BaseModel, Field

from flowgentic.utils.telemetry.extractor import Extractor
from .schemas import (
	TokenUsage,
	MessageInfo,
	ToolCallInfo,
	ModelMetadata,
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

	def _store_records(self, node_name: str, record):
		timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[
			:-3
		]  # Millisecond precision
		key = f"{node_name}_{timestamp}"
		self._records[key] = record

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
		node_name, record = self.extractor._state_extraction(
			node_name=node_name,
			state_before=state_before,
			state_after=state_after,
			total_messages_before=total_messages_before,
			start_time=start_time,
			end_time=end_time,
			node_func=node_func,
		)
		self._store_records(node_name, record)

	def record_supervisor_event(
		self,
		*,
		state: Any,
		destination: str,
		task_description: str,
		node_name: str = "supervisor",
	) -> None:
		"""
		Record a lightweight execution entry for the supervisor when it routes work.

		"""
		start_time = datetime.now()
		end_time = start_time

		# Extract messages count from state (supports Pydantic model or dict)
		if hasattr(state, "messages"):
			messages_list = state.messages
		elif isinstance(state, dict) and "messages" in state:
			messages_list = state["messages"]
		else:
			messages_list = []

		total_messages = len(messages_list) if isinstance(messages_list, list) else 0

		# Create a synthetic message entry describing the routing decision
		route_msg = MessageInfo(
			message_type="SupervisorRoute",
			content=f"transfer_to_{destination}: {task_description[:300]}",
			role="assistant",
			has_tool_calls=False,
			timestamp=datetime.now().isoformat(),
		)

		record = NodeExecutionRecord(
			node_name=node_name,
			description="Supervisor routing decision",
			start_time=start_time,
			end_time=end_time,
			duration_seconds=0.0,
			total_messages_before=total_messages,
			total_messages_after=total_messages,
			new_messages_count=0,
			messages_added=[route_msg],
			model_metadata=None,
			final_response=f"Command: routing to {destination}",
			interleaved_thinking=[],
			tool_calls=[],
			tool_executions=[],
			token_usage=None,
			state_diff={},
			state_keys=(
				list(state.model_fields.keys())
				if hasattr(state, "model_fields")
				else (list(state.keys()) if isinstance(state, dict) else [])
			),
		)

		self._store_records(node_name, record)
		self._final_state = state

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

	def generate_report(self) -> None:
		"""Generates a human-readable Markdown report of the entire graph execution."""
		if not self._all_nodes:
			raise ValueError(
				"You need to provide the the nodes for the graph to the inspector"
			)
		else:
			report_generator = ReportGenerator(
				final_state=self._final_state,
				records=self._records,
				start_time=self._start_time,
			).generate_report(all_nodes=self._all_nodes)
