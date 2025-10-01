import inspect
import sys
import time
from datetime import datetime
from copy import deepcopy
from typing import List, Dict, Any, Optional, Callable
from pydantic import BaseModel, Field

# --- Pydantic Models for Data Structure ---


class TokenUsage(BaseModel):
	"""Token usage statistics for a model call."""

	input_tokens: int = 0
	output_tokens: int = 0
	total_tokens: int = 0
	cache_read_tokens: int = 0
	reasoning_tokens: int = 0


class MessageInfo(BaseModel):
	"""Detailed information about a message in the conversation."""

	message_type: str  # SystemMessage, HumanMessage, AIMessage, ToolMessage
	content: str
	role: Optional[str] = None
	message_id: Optional[str] = None
	tool_call_id: Optional[str] = None
	has_tool_calls: bool = False
	timestamp: Optional[str] = None


class ToolCallInfo(BaseModel):
	"""Represents a single tool call by an agent."""

	tool_name: str
	tool_args: Dict[str, Any]
	tool_call_id: Optional[str] = None


class ModelMetadata(BaseModel):
	"""Comprehensive model execution metadata."""

	model_name: Optional[str] = None
	finish_reason: Optional[str] = None
	system_fingerprint: Optional[str] = None
	service_tier: Optional[str] = None


class NodeExecutionRecord(BaseModel):
	"""Captures all introspection data for a single node's execution."""

	node_name: str
	description: Optional[str] = None
	start_time: datetime
	end_time: datetime
	duration_seconds: float

	# Message tracking
	total_messages_before: int = 0
	total_messages_after: int = 0
	new_messages_count: int = 0
	messages_added: List[MessageInfo] = []

	# Model information
	model_metadata: Optional[ModelMetadata] = None
	model_reasoning: Optional[str] = None

	# Tool usage
	tool_calls: List[ToolCallInfo] = []

	# Token usage
	token_usage: Optional[TokenUsage] = None

	# State tracking
	state_diff: Dict[str, Any] = Field(
		default_factory=dict, description="Changes to the state after node execution."
	)
	state_keys: List[str] = []


class GraphExecutionReport(BaseModel):
	"""The final report containing all execution records and summary."""

	graph_start_time: datetime
	graph_end_time: Optional[datetime] = None
	total_duration_seconds: Optional[float] = None
	node_records: List[NodeExecutionRecord] = []
	final_state: Optional[Dict[str, Any]] = None

	# Aggregate statistics
	total_tokens_used: int = 0
	total_tool_calls: int = 0
	total_messages: int = 0
	models_used: List[str] = []


# --- Core Introspection Logic ---


class GraphIntrospector:
	"""
	A class to instrument and report on a LangGraph workflow execution.

	It provides a decorator (`introspect_node`) that wraps each graph node
	to record execution details seamlessly.
	"""

	def __init__(self):
		self._start_time = datetime.now()
		self._records: List[NodeExecutionRecord] = []
		self._final_state: Optional[BaseModel[str, Any]] = None

	def _get_state_diff(self, before_state, after_state) -> Dict:
		"""Calculates the difference between two state objects (Pydantic models or dicts)."""
		diff = {}

		# Convert Pydantic models to dicts
		if hasattr(before_state, "model_dump"):
			before_dict = before_state.model_dump()
		elif isinstance(before_state, dict):
			before_dict = before_state
		else:
			before_dict = (
				vars(before_state) if hasattr(before_state, "__dict__") else {}
			)

		if hasattr(after_state, "model_dump"):
			after_dict = after_state.model_dump()
		elif isinstance(after_state, dict):
			after_dict = after_state
		else:
			after_dict = vars(after_state) if hasattr(after_state, "__dict__") else {}

		all_keys = set(before_dict.keys()) | set(after_dict.keys())
		for key in all_keys:
			before_val = before_dict.get(key)
			after_val = after_dict.get(key)
			if before_val != after_val:
				diff[key] = {
					"from": str(before_val)[:300]
					if not isinstance(before_val, (list, dict))
					else f"[{type(before_val).__name__}]",
					"to": str(after_val)[:300]
					if not isinstance(after_val, (list, dict))
					else f"[{type(after_val).__name__}]",
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

	def _extract_token_usage(self, message) -> Optional[TokenUsage]:
		"""Extract token usage information from message metadata."""
		if not hasattr(message, "usage_metadata"):
			return None

		usage = message.usage_metadata
		if isinstance(usage, dict):
			input_details = usage.get("input_token_details", {})
			output_details = usage.get("output_token_details", {})

			return TokenUsage(
				input_tokens=usage.get("input_tokens", 0),
				output_tokens=usage.get("output_tokens", 0),
				total_tokens=usage.get("total_tokens", 0),
				cache_read_tokens=input_details.get("cache_read", 0)
				if isinstance(input_details, dict)
				else 0,
				reasoning_tokens=output_details.get("reasoning", 0)
				if isinstance(output_details, dict)
				else 0,
			)
		return None

	def _extract_model_metadata(self, message) -> Optional[ModelMetadata]:
		"""Extract model metadata from message."""
		if not hasattr(message, "response_metadata"):
			return None

		metadata = message.response_metadata
		if isinstance(metadata, dict):
			return ModelMetadata(
				model_name=metadata.get("model_name"),
				finish_reason=metadata.get("finish_reason"),
				system_fingerprint=metadata.get("system_fingerprint"),
				service_tier=metadata.get("service_tier"),
			)
		return None

	def introspect_node(self, node_func: Callable) -> Callable:
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
			elif isinstance(state_before, dict):
				messages_before = state_before.get("messages", [])
			else:
				messages_before = []
			total_messages_before = (
				len(messages_before) if isinstance(messages_before, list) else 0
			)

			# Execute the original node function
			state_after = await node_func(state)

			end_time = datetime.now()

			# Count messages after execution - handle Pydantic model
			if hasattr(state_after, "messages"):
				messages_after = state_after.messages
			elif isinstance(state_after, dict):
				messages_after = state_after.get("messages", [])
			else:
				messages_after = []
			total_messages_after = (
				len(messages_after) if isinstance(messages_after, list) else 0
			)
			new_messages_count = total_messages_after - total_messages_before

			# --- Data Extraction ---
			model_metadata = None
			reasoning = None
			tool_calls_info = []
			token_usage = None
			messages_added = []

			# Extract information from new messages
			if isinstance(messages_after, list) and new_messages_count > 0:
				new_messages = messages_after[-new_messages_count:]

				for msg in new_messages:
					# Extract message info
					msg_info = self._extract_message_info(msg)
					messages_added.append(msg_info)

					# Extract tool calls from AIMessage
					if hasattr(msg, "tool_calls") and msg.tool_calls:
						reasoning = (
							str(msg.content)
							if msg.content
							else "No reasoning text provided."
						)
						for tc in msg.tool_calls:
							# Handle both dict and object formats
							if isinstance(tc, dict):
								tool_name = tc.get("name")
								tool_args = tc.get("args", {})
								tool_call_id = tc.get("id")
							else:
								tool_name = getattr(tc, "name", None)
								tool_args = getattr(tc, "args", {})
								tool_call_id = getattr(tc, "id", None)

							tool_calls_info.append(
								ToolCallInfo(
									tool_name=tool_name,
									tool_args=tool_args,
									tool_call_id=tool_call_id,
								)
							)

					# Extract token usage (usually on AIMessage)
					if token_usage is None:
						token_usage = self._extract_token_usage(msg)

					# Extract model metadata (usually on AIMessage)
					if model_metadata is None:
						model_metadata = self._extract_model_metadata(msg)

			# Get state keys - handle Pydantic model
			if hasattr(state_after, "model_fields"):
				state_keys = list(state_after.model_fields.keys())
			elif hasattr(state_after, "__dict__"):
				state_keys = list(vars(state_after).keys())
			elif isinstance(state_after, dict):
				state_keys = list(state_after.keys())
			else:
				state_keys = []

			# Create and store the execution record
			record = NodeExecutionRecord(
				node_name=node_func.__name__,
				description=inspect.getdoc(node_func),
				start_time=start_time,
				end_time=end_time,
				duration_seconds=round((end_time - start_time).total_seconds(), 4),
				total_messages_before=total_messages_before,
				total_messages_after=total_messages_after,
				new_messages_count=new_messages_count,
				messages_added=messages_added,
				model_metadata=model_metadata,
				model_reasoning=reasoning,
				tool_calls=tool_calls_info,
				token_usage=token_usage,
				state_diff=self._get_state_diff(state_before, state_after),
				state_keys=state_keys,
			)
			self._records.append(record)
			self._final_state = state_after  # Continuously update the final state

			return state_after

		return wrapper

	def generate_report(self, output_path: str = "graph_execution_report.md"):
		"""Generates a human-readable Markdown report of the entire graph execution."""
		end_time = datetime.now()

		# Calculate aggregate statistics
		total_tokens = sum(
			(r.token_usage.total_tokens if r.token_usage else 0) for r in self._records
		)
		total_tool_calls = sum(len(r.tool_calls) for r in self._records)
		total_messages = self._records[-1].total_messages_after if self._records else 0
		models_used = list(
			set(
				r.model_metadata.model_name
				for r in self._records
				if r.model_metadata and r.model_metadata.model_name
			)
		)

		report_data = GraphExecutionReport(
			graph_start_time=self._start_time,
			graph_end_time=end_time,
			total_duration_seconds=round(
				(end_time - self._start_time).total_seconds(), 4
			),
			node_records=self._records,
			final_state=self._final_state.model_dump(),
			total_tokens_used=total_tokens,
			total_tool_calls=total_tool_calls,
			total_messages=total_messages,
			models_used=models_used,
		)

		with open(output_path, "w", encoding="utf-8") as f:
			f.write(f"# 📊 LangGraph Execution Report\n\n")
			f.write(
				f"**Generated on:** `{report_data.graph_start_time.strftime('%Y-%m-%d %H:%M:%S')}`\n"
			)
			f.write(
				f"**Total Duration:** `{report_data.total_duration_seconds} seconds`\n\n"
			)

			# Aggregate Statistics
			f.write("## 📈 Aggregate Statistics\n\n")
			f.write(f"- **Total Tokens Used:** `{report_data.total_tokens_used:,}`\n")
			f.write(f"- **Total Tool Calls:** `{report_data.total_tool_calls}`\n")
			f.write(f"- **Total Messages:** `{report_data.total_messages}`\n")
			f.write(
				f"- **Models Used:** `{', '.join(report_data.models_used) if report_data.models_used else 'None'}`\n"
			)
			f.write(f"- **Number of Nodes:** `{len(report_data.node_records)}`\n\n")

			f.write(f"--- \n\n")

			f.write("## 📝 Execution Summary\n\n")
			f.write(
				"| Node Name           | Duration (s) | Tokens | Tools | New Messages |\n"
			)
			f.write(
				"|---------------------|--------------|--------|-------|---------------|\n"
			)
			for record in report_data.node_records:
				tools_count = len(record.tool_calls)
				tokens = record.token_usage.total_tokens if record.token_usage else 0
				f.write(
					f"| `{record.node_name}` | {record.duration_seconds:<12.4f} | {tokens:<6} | {tools_count:<5} | {record.new_messages_count:<13} |\n"
				)
			f.write("\n\n")

			f.write("## 🔍 Node Details\n\n")
			for i, record in enumerate(report_data.node_records):
				f.write(f"--- \n\n")
				f.write(f"### {i + 1}. Node: `{record.node_name}`\n\n")
				if record.description:
					f.write(f"**Description:**\n```\n{record.description}\n```\n\n")
				f.write(
					f"- **Timestamp:** `{record.start_time.strftime('%H:%M:%S.%f')[:-3]}`\n"
				)
				f.write(f"- **Duration:** `{record.duration_seconds} seconds`\n")
				f.write(
					f"- **Messages Before/After:** `{record.total_messages_before}` → `{record.total_messages_after}` (➕ {record.new_messages_count})\n"
				)
				f.write(f"- **State Keys:** `{', '.join(record.state_keys)}`\n")

				if record.model_metadata:
					f.write(f"\n**🤖 Model Information:**\n")
					f.write(f"- **Model Name:** `{record.model_metadata.model_name}`\n")
					if record.model_metadata.finish_reason:
						f.write(
							f"- **Finish Reason:** `{record.model_metadata.finish_reason}`\n"
						)
					if record.model_metadata.system_fingerprint:
						f.write(
							f"- **System Fingerprint:** `{record.model_metadata.system_fingerprint}`\n"
						)

				if record.token_usage:
					f.write(f"\n**📊 Token Usage:**\n")
					f.write(
						f"- **Input Tokens:** `{record.token_usage.input_tokens:,}`\n"
					)
					f.write(
						f"- **Output Tokens:** `{record.token_usage.output_tokens:,}`\n"
					)
					f.write(
						f"- **Total Tokens:** `{record.token_usage.total_tokens:,}`\n"
					)
					if record.token_usage.cache_read_tokens > 0:
						f.write(
							f"- **Cache Read Tokens:** `{record.token_usage.cache_read_tokens:,}`\n"
						)
					if record.token_usage.reasoning_tokens > 0:
						f.write(
							f"- **Reasoning Tokens:** `{record.token_usage.reasoning_tokens:,}`\n"
						)

				if record.model_reasoning:
					f.write(
						f"\n**🤔 Model Reasoning:**\n```text\n{record.model_reasoning}\n```\n"
					)

				if record.tool_calls:
					f.write(f"\n**🛠️ Tool Calls ({len(record.tool_calls)}):**\n")
					for idx, tc in enumerate(record.tool_calls, 1):
						f.write(f"{idx}. **Tool:** `{tc.tool_name}`\n")
						if tc.tool_call_id:
							f.write(f"   - **Call ID:** `{tc.tool_call_id}`\n")
						f.write(f"   - **Arguments:** `{tc.tool_args}`\n")

				if record.messages_added:
					f.write(
						f"\n**💬 Messages Added ({len(record.messages_added)}):**\n"
					)
					for idx, msg in enumerate(record.messages_added, 1):
						f.write(f"{idx}. **{msg.message_type}**")
						if msg.message_id:
							f.write(f" (ID: `{msg.message_id[:20]}...`)")
						f.write(f"\n")
						if msg.role:
							f.write(f"   - **Role:** `{msg.role}`\n")
						if msg.has_tool_calls:
							f.write(f"   - **Has Tool Calls:** ✅\n")
						if msg.tool_call_id:
							f.write(f"   - **Tool Call ID:** `{msg.tool_call_id}`\n")
						f.write(
							f"   - **Content:** `{msg.content[:200]}{'...' if len(msg.content) > 200 else ''}`\n"
						)

				if record.state_diff:
					f.write(f"\n**🔄 State Changes:**\n")
					f.write("```json\n")
					import json

					f.write(json.dumps(record.state_diff, indent=2))
					f.write("\n```\n\n")

			f.write(f"--- \n\n")
			f.write("## ✅ Final State Summary\n\n")
			if self._final_state:
				# Get state keys - handle Pydantic model
				if hasattr(self._final_state, "model_fields"):
					state_keys = list(self._final_state.model_fields.keys())
					f.write(f"**State Keys:** `{', '.join(state_keys)}`\n\n")

					# Show summary of final state
					for key in state_keys:
						value = getattr(self._final_state, key, None)
						if key == "messages" and isinstance(value, list):
							f.write(f"- **{key}:** {len(value)} messages\n")
						elif isinstance(value, (list, dict)):
							f.write(
								f"- **{key}:** {type(value).__name__} with {len(value)} items\n"
							)
						else:
							f.write(
								f"- **{key}:** `{str(value)[:100]}{'...' if len(str(value)) > 100 else ''}`\n"
							)
				elif hasattr(self._final_state, "__dict__"):
					state_dict = vars(self._final_state)
					state_keys = list(state_dict.keys())
					f.write(f"**State Keys:** `{', '.join(state_keys)}`\n\n")

					for key, value in state_dict.items():
						if key == "messages" and isinstance(value, list):
							f.write(f"- **{key}:** {len(value)} messages\n")
						elif isinstance(value, (list, dict)):
							f.write(
								f"- **{key}:** {type(value).__name__} with {len(value)} items\n"
							)
						else:
							f.write(
								f"- **{key}:** `{str(value)[:100]}{'...' if len(str(value)) > 100 else ''}`\n"
							)
				elif isinstance(self._final_state, dict):
					f.write(
						f"**State Keys:** `{', '.join(self._final_state.keys())}`\n\n"
					)

					for key, value in self._final_state.items():
						if key == "messages" and isinstance(value, list):
							f.write(f"- **{key}:** {len(value)} messages\n")
						elif isinstance(value, (list, dict)):
							f.write(
								f"- **{key}:** {type(value).__name__} with {len(value)} items\n"
							)
						else:
							f.write(
								f"- **{key}:** `{str(value)[:100]}{'...' if len(str(value)) > 100 else ''}`\n"
							)

		print(f"✅ Introspection report saved to '{output_path}'")
