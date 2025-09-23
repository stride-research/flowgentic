from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, List


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


class WorkflowState(BaseModel):
	"""Main state model for the LangGraph workflow."""

	# Input
	user_input: str = ""

	# Preprocessing results
	validation_data: Optional[ValidationData] = None
	preprocessing_complete: bool = False

	# Agent execution results
	research_agent_output: Optional[AgentOutput] = None
	synthesis_agent_output: Optional[AgentOutput] = None

	# Context and intermediate data
	context: Optional[ContextData] = None

	# Final output
	final_output: str = ""
	workflow_complete: bool = False

	# Error handling
	errors: List[str] = Field(default_factory=list)
	current_stage: str = "initialized"
