from flowgentic.langGraph.main import LangraphIntegration
from ..utils.schemas import WorkflowState
from langgraph.graph import END, StateGraph
from .utils.actions_registry import ActionsRegistry

from .nodes import WorkflowNodes
from .edges import WorkflowEdges


import logging

logger = logging.getLogger(__name__)


class WorkflowBuilder:
	"""Builds and configures the complete workflow graph."""

	def __init__(self, agents_manager: LangraphIntegration):
		self.agents_manager = agents_manager
		self.tools_registry = ActionsRegistry(agents_manager)
		self.nodes = WorkflowNodes(agents_manager, self.tools_registry)
		self.edges = WorkflowEdges()

	# --8<-- [start:build-workflow]
	def build_workflow(self) -> StateGraph:
		"""Build and return the complete workflow graph."""
		# Register all tools first
		self.tools_registry._register_toolkit()

		# Create state graph
		workflow = StateGraph(WorkflowState)

		# Add all nodes
		all_nodes = self.nodes.get_all_nodes()
		for node_name, node_function in all_nodes.items():
			workflow.add_node(node_name, node_function)

		# Add conditional edges
		workflow.add_conditional_edges(
			"preprocess",
			self.edges.should_continue_after_preprocessing,
			{"research_agent": "research_agent", "error_handler": "error_handler"},
		)

		workflow.add_conditional_edges(
			"research_agent",
			self.edges.should_continue_after_research,
			{
				"context_preparation": "context_preparation",
				"error_handler": "error_handler",
			},
		)

		workflow.add_conditional_edges(
			"context_preparation",
			self.edges.should_continue_after_context,
			{"synthesis_agent": "synthesis_agent", "error_handler": "error_handler"},
		)

		workflow.add_conditional_edges(
			"synthesis_agent",
			self.edges.should_continue_after_synthesis,
			{"finalize_output": "finalize_output", "error_handler": "error_handler"},
		)

		# Add edges to END
		workflow.add_edge("finalize_output", END)
		workflow.add_edge("error_handler", END)

		# Set entry point
		workflow.set_entry_point("preprocess")

		return workflow
	# --8<-- [end:build-workflow]
