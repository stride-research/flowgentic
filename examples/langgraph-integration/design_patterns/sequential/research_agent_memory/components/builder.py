"""
Graph builder for memory-enabled sequential workflow.

This module assembles all components (nodes, edges, tools, memory) into
a complete LangGraph workflow.
"""

from langgraph.graph import StateGraph, END
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.memory import MemoryManager
from ..utils.schemas import WorkflowState
from .nodes import WorkflowNodes
from .edges import WorkflowEdges
from .utils.actions_registry import ActionsRegistry


class WorkflowBuilder:
	"""Assembles the memory-enabled workflow graph."""

	def __init__(
		self, agents_manager: LangraphIntegration, memory_manager: MemoryManager
	):
		self.agents_manager = agents_manager
		self.memory_manager = memory_manager
		self.tools_registry = ActionsRegistry(agents_manager)
		self.nodes = WorkflowNodes(agents_manager, self.tools_registry, memory_manager)
		self.edges = WorkflowEdges()

	def build_workflow(self) -> StateGraph:
		"""Build and return the complete memory-enabled workflow graph."""

		# Step 1: Register all tools and tasks
		print("🔧 Registering tools and tasks...")
		self.tools_registry._register_toolkit()

		# Step 2: Create the state graph
		print("📊 Creating state graph...")
		workflow = StateGraph(WorkflowState)

		# Step 3: Add all nodes
		print("🔗 Adding nodes...")
		for node_name, node_function in self.nodes.get_all_nodes().items():
			workflow.add_node(node_name, node_function)

		# Step 4: Define conditional edges (routing logic)
		print("🔀 Defining conditional edges...")
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

		workflow.add_edge("context_preparation", "synthesis_agent")

		workflow.add_conditional_edges(
			"synthesis_agent",
			self.edges.should_continue_after_synthesis,
			{"finalize_output": "finalize_output", "error_handler": "error_handler"},
		)

		# Step 5: Add terminal edges
		workflow.add_edge("finalize_output", END)
		workflow.add_edge("error_handler", END)

		# Step 6: Set entry point
		workflow.set_entry_point("preprocess")

		print("✅ Workflow graph built successfully with memory integration!")
		return workflow
