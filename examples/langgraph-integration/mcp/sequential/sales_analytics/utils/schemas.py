"""
Pydantic schemas for sales analytics workflow state management.
Follows same pattern as financial_advisor for consistency.
"""

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, Field
from typing import Annotated, Any, Dict, Optional, List
from enum import Enum


class AnalysisType(str, Enum):
	"""Types of sales analysis."""

	REVENUE = "revenue"
	PERFORMANCE = "performance"
	TRENDS = "trends"
	FORECASTING = "forecasting"


class QueryValidation(BaseModel):
	"""Model for query validation results."""

	is_valid: bool
	cleaned_query: str
	word_count: int
	timestamp: float
	analysis_type: Optional[AnalysisType] = None
	time_period: Optional[str] = None
	regions: List[str] = Field(default_factory=list)
	metadata: Dict[str, Any] = Field(default_factory=dict)


class SalesMetrics(BaseModel):
	"""Sales performance metrics."""

	total_revenue: Optional[float] = None
	growth_rate: Optional[float] = None
	average_deal_size: Optional[float] = None
	top_region: Optional[str] = None
	anomalies_detected: List[str] = Field(default_factory=list)
	key_insights: List[str] = Field(default_factory=list)


class AgentOutput(BaseModel):
	"""Standardized output from any agent node."""

	agent_name: str
	output_content: str
	execution_time: float
	tools_used: List[str] = Field(default_factory=list)
	success: bool = True
	error_message: Optional[str] = None
	structured_data: Dict[str, Any] = Field(default_factory=dict)


class AnalysisContext(BaseModel):
	"""Context passed between analysis stages."""

	database_results: str
	query_parameters: QueryValidation
	sales_metrics: Optional[SalesMetrics] = None
	processing_stage: str
	stage_sequence: int
	additional_context: Dict[str, Any] = Field(default_factory=dict)


class WorkflowState(BaseModel):
	"""Main state model for the sales analytics workflow."""

	# Input
	user_query: str = ""

	# Validation results
	query_validation: Optional[QueryValidation] = None
	validation_complete: bool = False

	# Agent execution results
	data_extraction_output: Optional[AgentOutput] = None
	analytics_output: Optional[AgentOutput] = None
	report_generation_output: Optional[AgentOutput] = None
	messages: Annotated[List[BaseMessage], add_messages] = []

	# Context and intermediate data
	analysis_context: Optional[AnalysisContext] = None

	# Final output
	final_report: str = ""
	workflow_complete: bool = False

	# Error handling
	errors: List[str] = Field(default_factory=list)
	current_stage: str = "initialized"
