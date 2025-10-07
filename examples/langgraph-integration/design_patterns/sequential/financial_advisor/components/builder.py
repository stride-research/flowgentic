from flowgentic.langGraph.main import LangraphIntegration
from ..utils.schemas import WorkflowState
from langgraph.graph import END, StateGraph
from .utils.actions_registry import ActionsRegistry

from .nodes import WorkflowNodes
from .edges import WorkflowEdges

from flowgentic.langGraph.telemetry import GraphIntrospector

import logging

logger = logging.getLogger(__name__)


class WorkflowBuilder:
	"""Builds and configures the complete financial advisory workflow graph."""

	def __init__(self, agents_manager: LangraphIntegration):
		self.agents_manager = agents_manager
		self.tools_registry = ActionsRegistry(agents_manager)
		self.nodes = WorkflowNodes(agents_manager, self.tools_registry)
		self.edges = WorkflowEdges()

	def _register_nodes_to_introspector(self):
		all_nodes = list(self.nodes.get_all_nodes().keys())
		self.agents_manager.agent_introspector._all_nodes = all_nodes

	def build_workflow(self) -> StateGraph:
		"""Build and return the complete workflow graph."""
		# Register all tools first
		self.tools_registry._register_toolkit()

		# Create state graph
		workflow = StateGraph(WorkflowState)

		# Add all nodes with introspection
		all_nodes = self.nodes.get_all_nodes()
		for node_name, node_function in all_nodes.items():
			node_function = self.agents_manager.agent_introspector.introspect_node(
				node_function, node_name=node_name
			)
			workflow.add_node(node_name, node_function)
		self._register_nodes_to_introspector()

		# Add conditional edges
		workflow.add_conditional_edges(
			"validate_input",
			self.edges.should_continue_after_validation,
			{
				"market_research_agent": "market_research_agent",
				"error_handler": "error_handler",
			},
		)

		workflow.add_conditional_edges(
			"market_research_agent",
			self.edges.should_continue_after_market_research,
			{
				"risk_context_prep": "risk_context_prep",
				"error_handler": "error_handler",
			},
		)

		workflow.add_conditional_edges(
			"risk_context_prep",
			self.edges.should_continue_after_risk_context_prep,
			{
				"risk_assessment_agent": "risk_assessment_agent",
				"error_handler": "error_handler",
			},
		)

		workflow.add_conditional_edges(
			"risk_assessment_agent",
			self.edges.should_continue_after_risk_assessment,
			{
				"strategy_context_prep": "strategy_context_prep",
				"error_handler": "error_handler",
			},
		)

		workflow.add_conditional_edges(
			"strategy_context_prep",
			self.edges.should_continue_after_strategy_context_prep,
			{
				"portfolio_strategy_agent": "portfolio_strategy_agent",
				"error_handler": "error_handler",
			},
		)

		workflow.add_conditional_edges(
			"portfolio_strategy_agent",
			self.edges.should_continue_after_portfolio_strategy,
			{"finalize_report": "finalize_report", "error_handler": "error_handler"},
		)

		# Add edges to END
		workflow.add_edge("finalize_report", END)
		workflow.add_edge("error_handler", END)

		# Set entry point
		workflow.set_entry_point("validate_input")

		return workflow
