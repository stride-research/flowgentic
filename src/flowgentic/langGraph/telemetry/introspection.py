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
from pydantic import BaseModel, Field
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.settings.extract_settings import APP_SETTINGS
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

	def _get_state_diff(self, before_state, after_state) -> Dict:
		"""Calculates the difference between two state objects (Pydantic models or dicts)."""
		diff = {}

		# Convert Pydantic models to dicts
		if hasattr(before_state, "model_dump"):  # If its pydantic  state
			before_dict = before_state.model_dump()
		else:
			raise ValueError("Your workflow state should be created with Pydantic")

		if hasattr(after_state, "model_dump"):
			after_dict = after_state.model_dump()
		else:
			raise ValueError("Your workflow state should be created with Pydantic")

		all_keys = set(before_dict.keys()) | set(
			after_dict.keys()
		)  # Extracting the unique keys
		for key in all_keys:
			before_val = before_dict.get(key)
			after_val = after_dict.get(key)
			if before_val != after_val:
				diff[key] = {
					"changed_from": str(before_val)[:300]
					if not isinstance(before_val, (list, dict))
					else f"[{before_val}]",
					"changed_to": str(after_val)[:300]
					if not isinstance(after_val, (list, dict))
					else f"[{after_val}]",
				}
		return diff

	def _extract_message_info(self, message) -> MessageInfo:
		"""Extract detailed information from a message object."""
		message_type = type(message).__name__
		content = ""

		if hasattr(message, "content"):
			content = str(message.content)[:500]  # Truncate long content

		return MessageInfo(
			message_type=message_type,
			content=content,
			role=getattr(message, "role", None),
			message_id=getattr(message, "id", None),
			tool_call_id=getattr(message, "tool_call_id", None),
			has_tool_calls=hasattr(message, "tool_calls") and bool(message.tool_calls),
			timestamp=datetime.now().isoformat(),
		)

	def _extract_token_usage(self, message: AIMessage) -> Optional[TokenUsage]:
		"""Extract token usage information from message metadata."""
		if not isinstance(message, AIMessage):
			logger.warning("Mesage passed is not instance of AI Message")
			return

		usage = message.usage_metadata
		if isinstance(usage, dict):
			input_details = usage.get("input_token_details", {})
			output_details = usage.get("output_token_details", {})

			return TokenUsage(
				input_tokens=usage.get("input_tokens", 0),
				output_tokens=usage.get("output_tokens", 0),
				total_tokens=usage.get("total_tokens", 0),
				cache_read_tokens=input_details.get("cache_read", 0),
				reasoning_tokens=output_details.get("reasoning", 0),
			)
		else:
			logger.warning(
				f"usage metadata attribute in message should be a dict. Instead is: {type(usage)}"
			)

	def _extract_model_metadata(self, message: AIMessage) -> Optional[ModelMetadata]:
		"""Extract model metadata from message."""

		metadata = message.response_metadata
		logger.debug(f"Message metadata is: {metadata}")
		if isinstance(metadata, dict):
			return ModelMetadata(
				model_name=metadata.get("model_name"),
				finish_reason=metadata.get("finish_reason"),
				system_fingerprint=metadata.get("system_fingerprint"),
				service_tier=metadata.get("service_tier"),
			)
		return None

	def _state_extraction(
		self,
		node_name: str,
		state_before: BaseModel,
		state_after: BaseModel,
		total_messages_before: int,
		start_time,
		end_time,
		node_func: callable,
	):
		if hasattr(state_after, "messages"):
			messages_after = state_after.messages
		else:
			logger.warning(f"State after doesnt have .message attribute")
			return
		logger.debug(f"Messages is: {messages_after}")

		total_messages_after = (
			len(messages_after) if isinstance(messages_after, list) else 0
		)
		new_messages_count = total_messages_after - total_messages_before

		# --- Data Extraction ---
		model_metadata = None
		final_response = None
		tool_calls_info: List[ToolCallInfo] = []
		tool_execution_info: List[ToolExecutionInfo] = []
		token_usage = TokenUsage()
		messages_added = []
		interleaved_thinking: List[str] = []

		# Extract information from new messages
		if isinstance(messages_after, list) and new_messages_count > 0:
			new_messages: List[AIMessage | HumanMessage | SystemMessage] = (
				messages_after[-new_messages_count:]
			)

			for i, msg in enumerate(new_messages):
				# Extract message info
				msg_info = self._extract_message_info(msg)
				messages_added.append(msg_info)

				# Extract tool calls from AIMessage
				if isinstance(msg, AIMessage):
					# Extract tool invokation info
					if hasattr(msg, "additional_kwargs") and msg.additional_kwargs:
						if msg.content:
							if i == len(new_messages) - 1:
								final_response = msg.content
							else:
								interleaved_thinking.append(msg.content)
						tool_calls = msg.additional_kwargs.get("tool_calls")
						if tool_calls:
							for tc in tool_calls:
								tool_function = tc.get("function")
								tool_args = json.loads(tool_function.get("arguments"))
								tool_name = tool_function.get("name")
								tool_call_id = tc.get("id")

								tool_call_info = ToolCallInfo(
									tool_name=tool_name,
									tool_args=tool_args,
									tool_call_id=tool_call_id,
								)
								tool_calls_info.append(tool_call_info)

					current_token_usage = self._extract_token_usage(msg)
					if current_token_usage:
						token_usage.input_tokens += current_token_usage.input_tokens
						token_usage.output_tokens += current_token_usage.output_tokens
						token_usage.total_tokens += current_token_usage.total_tokens
						token_usage.cache_read_tokens += (
							current_token_usage.cache_read_tokens
						)
						token_usage.reasoning_tokens += (
							current_token_usage.reasoning_tokens
						)

					if model_metadata is None:
						model_metadata = self._extract_model_metadata(msg)
						logger.debug(f"Model metadata has been extracted")
				elif isinstance(msg, ToolMessage):
					logger.debug(f"DICT OF TOOL MESSAGE IS: {msg.__dict__}")
					tool_name = msg.name
					tool_status = msg.status
					tool_call_id = msg.tool_call_id
					tool_response = msg.content
					tool_execution_info.append(
						ToolExecutionInfo(
							tool_name=tool_name,
							tool_status=tool_status,
							tool_call_id=tool_call_id,
							tool_response=tool_response,
						)
					)

		# Get state keys - handle Pydantic model
		if hasattr(state_after, "model_fields"):
			state_keys = list(state_after.model_fields.keys())
		else:
			raise ValueError(
				f"Model fields keys is not available for state after with type: {type(state_after)}"
			)

		# Create and store the execution record
		record = NodeExecutionRecord(
			node_name=node_name,
			description=inspect.getdoc(node_func),
			start_time=start_time,
			end_time=end_time,
			duration_seconds=round((end_time - start_time).total_seconds(), 4),
			total_messages_before=total_messages_before,
			total_messages_after=total_messages_after,
			new_messages_count=new_messages_count,
			messages_added=messages_added,
			model_metadata=model_metadata,
			final_response=final_response,
			interleaved_thinking=interleaved_thinking,
			tool_calls=tool_calls_info,
			tool_executions=tool_execution_info,
			token_usage=token_usage,
			state_diff=self._get_state_diff(state_before, state_after),
			state_keys=state_keys,
		)
		self._records[node_name] = record
		self._final_state = state_after  # Continuously update the final state

	def introspect_node(self, node_func: Callable, node_name: str) -> Callable:
		"""
		Decorator to wrap a LangGraph node function for introspection.

		This decorator times the node's execution, captures its inputs and outputs,
		and extracts metadata like tool calls and model reasoning from the state.
		"""

		# ASK AYMEN: DOES THIS NEED TO BE A FUNCTION TASK
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
			self._state_extraction(
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
