import inspect
import json
from datetime import datetime
from typing import List, Dict, Optional
from langchain_core.messages import (
	AIMessage,
	HumanMessage,
	SystemMessage,
	ToolMessage,
)
from langgraph.types import Command
from pydantic import BaseModel, Field

from .schemas import (
	TokenUsage,
	MessageInfo,
	ToolCallInfo,
	ModelMetadata,
	NodeExecutionRecord,
	ToolExecutionInfo,
)
import logging

logger = logging.getLogger(__name__)


class Extractor:
	def __init__(self) -> None:
		pass

	def _get_state_diff(self, before_state, after_state) -> Dict:
		"""Calculates the difference between two state objects (Pydantic models or dicts)."""
		diff = {}

		# Convert Pydantic models to dicts, or use dict directly
		if hasattr(before_state, "model_dump"):  # If it's a pydantic state
			before_dict = before_state.model_dump()
		elif isinstance(before_state, dict):
			before_dict = before_state
		else:
			logger.warning(
				f"State before is neither Pydantic nor dict: {type(before_state)}"
			)
			return diff

		if hasattr(after_state, "model_dump"):
			after_dict = after_state.model_dump()
		elif isinstance(after_state, dict):
			after_dict = after_state
		else:
			logger.warning(
				f"State after is neither Pydantic nor dict: {type(after_state)}"
			)
			return diff

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

	def _final_state_extraction(
		self,
		node_name: str,
		state_before: BaseModel,
		state_after: BaseModel,
		total_messages_before: int,
		start_time,
		end_time,
		node_func: callable,
	):
		logger.debug(f"Extracting final state for node with name: {node_name}")
		# Handle both Pydantic models and dicts
		if hasattr(state_after, "messages"):
			messages_after = state_after.messages
		elif isinstance(state_after, dict):
			if "messages" in state_after:
				messages_after = state_after["messages"]
			else:
				messages_after = []
		else:
			logger.warning(
				f"State after doesn't have messages attribute/key: {type(state_after)}"
			)
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

		# Get state keys - handle Pydantic model and dicts
		if hasattr(state_after, "model_fields"):
			state_keys = list(state_after.model_fields.keys())
		elif isinstance(state_after, dict):
			state_keys = list(state_after.keys())
		else:
			logger.warning(
				f"State is neither Pydantic model nor dict. Type: {type(state_after)}"
			)
			state_keys = []

		# Create and store the execution record
		timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[
			:-3
		]  # Millisecond precision
		node_name_detailed = f"{node_name}_{timestamp}"
		record = NodeExecutionRecord(
			node_name=node_name,
			node_name_detailed=node_name_detailed,
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
		self._final_state = state_after  # Continuously update the final state
		return node_name_detailed, record
