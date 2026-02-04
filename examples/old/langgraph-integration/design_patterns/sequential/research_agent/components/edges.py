from ..utils.schemas import WorkflowState


class WorkflowEdges:
	"""Defines the conditional logic for workflow edges."""

	@staticmethod
	def should_continue_after_preprocessing(state: WorkflowState) -> str:
		"""Determine next step after preprocessing."""
		if state.preprocessing_complete and state.validation_data.is_valid:
			return "research_agent"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_research(state: WorkflowState) -> str:
		"""Determine next step after research agent."""
		if state.research_agent_output and state.research_agent_output.success:
			return "context_preparation"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_context(state: WorkflowState) -> str:
		"""Determine next step after context preparation."""
		if state.context:
			return "synthesis_agent"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_synthesis(state: WorkflowState) -> str:
		"""Determine next step after synthesis agent."""
		if state.synthesis_agent_output and state.synthesis_agent_output.success:
			return "finalize_output"
		else:
			return "error_handler"
