from ..utils.schemas import WorkflowState


class WorkflowEdges:
	"""Defines the conditional logic for financial workflow edges."""

	@staticmethod
	def should_continue_after_validation(state: WorkflowState) -> str:
		"""Determine next step after input validation."""
		if state.preprocessing_complete and state.investment_validation.is_valid:
			return "market_research_agent"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_market_research(state: WorkflowState) -> str:
		"""Determine next step after market research agent."""
		if state.market_research_output and state.market_research_output.success:
			return "risk_context_prep"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_risk_context_prep(state: WorkflowState) -> str:
		"""Determine next step after risk context preparation."""
		if state.portfolio_context:
			return "risk_assessment_agent"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_risk_assessment(state: WorkflowState) -> str:
		"""Determine next step after risk assessment agent."""
		if state.risk_assessment_output and state.risk_assessment_output.success:
			return "strategy_context_prep"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_strategy_context_prep(state: WorkflowState) -> str:
		"""Determine next step after strategy context preparation."""
		if state.portfolio_context and state.portfolio_context.agent_sequence >= 2:
			return "portfolio_strategy_agent"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_portfolio_strategy(state: WorkflowState) -> str:
		"""Determine next step after portfolio strategy agent."""
		if state.portfolio_strategy_output and state.portfolio_strategy_output.success:
			return "finalize_report"
		else:
			return "error_handler"
