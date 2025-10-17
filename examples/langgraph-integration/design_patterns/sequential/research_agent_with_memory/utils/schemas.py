"""
State schemas for memory-enabled sequential workflow.

This module extends the base sequential workflow schemas with memory capabilities,
allowing the workflow to maintain context across stages and retrieve relevant
information from conversation history.
"""

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, Field
from typing import Annotated, Any, Dict, Optional, List


class ValidationData(BaseModel):
	"""Model for input validation results."""

	is_valid: bool
	cleaned_input: str
	word_count: int
	timestamp: float
	metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
	"""Model for agent execution results."""

	agent_name: str
	output_content: str
	execution_time: float
	tools_used: List[str] = Field(default_factory=list)
	success: bool = True
	error_message: Optional[str] = None


class ContextData(BaseModel):
	"""Model for context passed between workflow stages."""

	previous_analysis: str
	input_metadata: ValidationData
	processing_stage: str
	agent_sequence: int
	additional_context: Dict[str, Any] = Field(default_factory=dict)


class MemoryStats(BaseModel):
	"""Model for memory statistics and health metrics."""

	total_messages: int = 0
	system_messages: int = 0
	human_messages: int = 0
	ai_messages: int = 0
	memory_efficiency: float = 0.0
	average_importance: float = 0.0
	interaction_count: int = 0


class WorkflowState(BaseModel):
	"""Memory-enabled state model for the LangGraph workflow.
	
	This state extends the base sequential workflow with memory management
	capabilities, allowing agents to access relevant context from previous
	stages and maintain conversation history.
	"""

	# Input
	user_input: str = ""
	user_id: str = "default_user"

	# Preprocessing results
	validation_data: Optional[ValidationData] = None
	preprocessing_complete: bool = False

	# Agent execution results
	research_agent_output: Optional[AgentOutput] = None
	synthesis_agent_output: Optional[AgentOutput] = None
	messages: Annotated[List[BaseMessage], add_messages] = []

	# Context and intermediate data
	context: Optional[ContextData] = None

	# Memory-specific fields
	memory_context: Dict[str, Any] = Field(default_factory=dict)
	memory_stats: Optional[MemoryStats] = None
	relevant_memory: List[BaseMessage] = Field(default_factory=list)

	# Final output
	final_output: str = ""
	workflow_complete: bool = False

	# Error handling
	errors: List[str] = Field(default_factory=list)
	current_stage: str = "initialized"

	# Memory health tracking
	memory_operations: List[str] = Field(default_factory=list)
