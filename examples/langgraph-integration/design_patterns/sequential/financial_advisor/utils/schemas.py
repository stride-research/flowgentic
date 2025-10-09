from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from pydantic import BaseModel, Field
from typing import Annotated, Any, Dict, Optional, List
from enum import Enum


class RiskLevel(str, Enum):
	"""Risk levels for investment portfolios."""

	LOW = "low"
	MODERATE = "moderate"
	HIGH = "high"
	VERY_HIGH = "very_high"


class InvestmentValidation(BaseModel):
	"""Model for investment query validation results."""

	is_valid: bool
	cleaned_input: str
	word_count: int
	timestamp: float
	detected_tickers: List[str] = Field(default_factory=list)
	investment_amount: Optional[float] = None
	time_horizon: Optional[str] = None
	risk_tolerance: Optional[RiskLevel] = None
	metadata: Dict[str, Any] = Field(default_factory=dict)


class MarketData(BaseModel):
	"""Market research data."""

	ticker: str
	current_price: float
	market_cap: float
	pe_ratio: float
	dividend_yield: float
	volatility: float
	sector: str
	analyst_rating: str
	news_sentiment: float


class RiskMetrics(BaseModel):
	"""Risk assessment metrics."""

	portfolio_variance: float
	sharpe_ratio: float
	max_drawdown: float
	beta: float
	value_at_risk: float
	risk_score: float
	compliance_status: str
	regulatory_flags: List[str] = Field(default_factory=list)


class AgentOutput(BaseModel):
	"""Standardized output from any agent node."""

	agent_name: str
	output_content: str
	execution_time: float
	tools_used: List[str] = Field(default_factory=list)
	success: bool = True
	error_message: Optional[str] = None
	structured_data: Dict[str, Any] = Field(default_factory=dict)


class PortfolioContext(BaseModel):
	"""Context passed between financial analysis stages."""

	market_analysis: str
	investment_parameters: InvestmentValidation
	risk_metrics: Optional[RiskMetrics] = None
	processing_stage: str
	agent_sequence: int
	portfolio_constraints: Dict[str, Any] = Field(default_factory=dict)
	additional_context: Dict[str, Any] = Field(default_factory=dict)


class WorkflowState(BaseModel):
	"""Main state model for the financial advisory workflow."""

	# Input
	user_input: str = ""

	# Validation results
	investment_validation: Optional[InvestmentValidation] = None
	preprocessing_complete: bool = False

	# Agent execution results
	market_research_output: Optional[AgentOutput] = None
	risk_assessment_output: Optional[AgentOutput] = None
	portfolio_strategy_output: Optional[AgentOutput] = None
	messages: Annotated[List[BaseMessage], add_messages] = []

	# Context and intermediate data
	portfolio_context: Optional[PortfolioContext] = None

	# Final output
	final_report: str = ""
	workflow_complete: bool = False

	# Error handling
	errors: List[str] = Field(default_factory=list)
	current_stage: str = "initialized"
