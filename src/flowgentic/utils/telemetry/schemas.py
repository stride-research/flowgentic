from typing import List, Dict, Any, Optional, Callable
from pydantic import BaseModel, Field
from datetime import datetime


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


class ToolExecutionInfo(BaseModel):
	"""Represents a single tool call by an agent."""

	tool_name: str
	tool_status: str
	tool_call_id: str
	tool_response: str


class ModelMetadata(BaseModel):
	"""Comprehensive model execution metadata."""

	model_name: Optional[str] = None
	finish_reason: Optional[str] = None
	system_fingerprint: Optional[str] = None
	service_tier: Optional[str] = None


class NodeExecutionRecord(BaseModel):
	"""Captures all introspection data for a single node's execution."""

	node_name: str
	node_name_detailed: str
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
	final_response: Optional[str] = None
	interleaved_thinking: Optional[List[str]] = None

	# Tool usage
	tool_calls: List[ToolCallInfo] = []
	tool_executions: List[ToolExecutionInfo] = []

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
	total_tool_executions: int = 0
	total_messages: int = 0
	models_used: List[str] = []
