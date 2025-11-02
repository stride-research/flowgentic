"""
Conditional routing logic for memory-enabled sequential workflow.

This module defines the edge functions that determine workflow routing
based on state conditions.
"""

from ..utils.schemas import WorkflowState


class WorkflowEdges:
	"""Conditional routing logic for the workflow."""

	@staticmethod
	def should_continue_after_preprocessing(state: WorkflowState) -> str:
		"""Route after preprocessing based on validation success."""
		if state.preprocessing_complete and state.validation_data and state.validation_data.is_valid:
			return "research_agent"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_research(state: WorkflowState) -> str:
		"""Route after research based on success."""
		if state.research_agent_output and state.research_agent_output.success:
			return "context_preparation"
		else:
			return "error_handler"

	@staticmethod
	def should_continue_after_synthesis(state: WorkflowState) -> str:
		"""Route after synthesis based on success."""
		if state.synthesis_agent_output and state.synthesis_agent_output.success:
			return "finalize_output"
		else:
			return "error_handler"
