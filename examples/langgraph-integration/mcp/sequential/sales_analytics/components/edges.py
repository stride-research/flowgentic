"""
Conditional logic for workflow edges.
"""

from ..utils.schemas import WorkflowState


class WorkflowEdges:
	"""Defines the conditional logic for sales analytics workflow edges."""

	@staticmethod
	def should_continue_after_validation(state: WorkflowState) -> str:
		"""Determine next step after validation."""
		if state.validation_complete and state.query_validation.is_valid:
			return "data_extraction_agent"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_data_extraction(state: WorkflowState) -> str:
		"""Determine next step after data extraction."""
		if state.data_extraction_output and state.data_extraction_output.success:
			return "analytics_context_prep"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_analytics_context(state: WorkflowState) -> str:
		"""Determine next step after analytics context prep."""
		if state.analysis_context:
			return "analytics_agent"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_analytics(state: WorkflowState) -> str:
		"""Determine next step after analytics."""
		if state.analytics_output and state.analytics_output.success:
			return "report_context_prep"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_report_context(state: WorkflowState) -> str:
		"""Determine next step after report context prep."""
		if state.analysis_context and state.analysis_context.stage_sequence >= 2:
			return "report_generation_agent"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_report_generation(state: WorkflowState) -> str:
		"""Determine next step after report generation."""
		if state.report_generation_output and state.report_generation_output.success:
			return "finalize_report"
		else:
			return "error_handler"
