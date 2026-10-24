"""
Dynamic Graph Base Class for Runtime Graph Modification

Provides a reusable base class for creating LangGraphs that can be modified
at runtime with expand, reduce, and update operations.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Callable, TypedDict
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from flowgentic.langGraph.main import LangraphIntegration

logger = logging.getLogger(__name__)


class MutableGraph(ABC):
	"""
	Abstract base class for dynamic runtime graph modification.

	Provides core functionality for:
	- Expanding graphs (adding nodes)
	- Reducing graphs (removing nodes)
	- Updating graphs (replacing nodes)

	Subclasses must implement:
	- _register_available_nodes(): Register nodes specific to your graph
	- _get_state_schema(): Return the TypedDict state schema

	Subclasses can optionally override:
	- _add_edges(): Customize edge creation logic (default is sequential)
	"""

	def __init__(
		self, agents_manager: LangraphIntegration, initial_active_nodes: List[str]
	):
		"""
		Initialize the dynamic graph.

		Args:
			agents_manager: LangraphIntegration instance for AsyncFlow execution
			initial_active_nodes: List of node names to start with (validates that nodes exist)
		"""
		logger.debug("Creating a MutableGraph")
		self.agents_manager = agents_manager
		self.available_nodes: Dict[str, Callable] = {}
		self.active_node_names: List[str] = []
		self.graph: Optional[CompiledStateGraph] = None

		# Register all available node functions
		self._register_available_nodes()

		# Validate that all requested nodes exist
		invalid_nodes = [
			n for n in initial_active_nodes if n not in self.available_nodes
		]
		if invalid_nodes:
			raise ValueError(
				f"Invalid nodes requested: {invalid_nodes}. "
				f"Available: {list(self.available_nodes.keys())}"
			)
		self.active_node_names = initial_active_nodes.copy()
		logger.debug(
			"Activated %d nodes: %s",
			len(self.active_node_names),
			self.active_node_names,
		)

	@abstractmethod
	def _register_available_nodes(self) -> None:
		"""
		Register all available node functions for this graph.

		Implementation should:
		1. Define node functions decorated with @asyncflow
		2. Add them to self.available_nodes dict

		Example:
			@self.agents_manager.execution_wrappers.asyncflow(
				flow_type=AsyncFlowType.FUNCTION_TASK
			)
			async def my_node(state: GraphState) -> GraphState:
				# node logic
				return updated_state

			self.available_nodes["my_node"] = my_node
		"""
		pass

	@abstractmethod
	def _get_state_schema(self) -> type:
		"""
		Return the TypedDict class used for graph state.

		Example:
			return GraphState
		"""
		pass

	async def rebuild_graph(self) -> None:
		"""
		Rebuild the entire graph with current active nodes.

		This is called after any modification (expand, reduce, update).
		The rebuild process is split into two phases:
		1. _add_nodes(): Add all active nodes to the workflow
		2. _add_edges(): Connect nodes with edges (can be overridden)
		"""
		logger.debug("Rebuilding the graph with nodes %s", self.active_node_names)

		if not self.active_node_names:
			logger.warning("No active nodes to build the graph from")
			self.graph = None
			return

		# Create new workflow with state schema
		state_schema = self._get_state_schema()
		workflow = StateGraph(state_schema)

		# Phase 1: Add nodes
		self._add_nodes(workflow)

		# Phase 2: Add edges (can be overridden for custom topology)
		self._add_edges(workflow)

		# Compile the graph
		self.graph = workflow.compile()
		logger.debug("Rebuilt the graph with %d nodes", len(self.active_node_names))

	def _add_nodes(self, workflow: StateGraph) -> None:
		"""
		Add all active nodes to the workflow.

		This method typically doesn't need to be overridden.

		Args:
			workflow: The StateGraph workflow to add nodes to
		"""
		for node_name in self.active_node_names:
			if node_name in self.available_nodes:
				workflow.add_node(node_name, self.available_nodes[node_name])
				logger.debug("Added node %s", node_name)
			else:
				logger.error("Node %s is not registered; leaving it out", node_name)

		# Set entry point to first node
		if self.active_node_names:
			workflow.set_entry_point(self.active_node_names[0])
			logger.debug("Entry point: %s", self.active_node_names[0])

	def _add_edges(self, workflow: StateGraph) -> None:
		"""
		Add edges to connect nodes in the workflow.

		DEFAULT IMPLEMENTATION: Sequential flow (node[i] -> node[i+1] -> ... -> END)

		Override this method to implement custom edge logic:
		- Conditional edges
		- Parallel execution
		- Complex routing

		Example override for conditional edges:
			def _add_edges(self, workflow: StateGraph) -> None:
				workflow.add_conditional_edges("decision_node", self.route_function)
				workflow.add_edge("process_node", END)

		Args:
			workflow: The StateGraph workflow to add edges to
		"""
		# Default: Sequential flow
		for i, node_name in enumerate(self.active_node_names):
			if i == len(self.active_node_names) - 1:
				# Last node connects to END
				workflow.add_edge(node_name, END)
				logger.debug("Edge %s -> END", node_name)
			else:
				# Connect to next node
				next_node = self.active_node_names[i + 1]
				workflow.add_edge(node_name, next_node)
				logger.debug("Edge %s -> %s", node_name, next_node)

	def register_node(self, node_name: str, node_function: Callable) -> bool:
		"""
		Register a new node function at runtime.

		This allows adding completely new nodes that weren't registered during initialization.
		The node function should already be decorated with @asyncflow.

		Args:
			node_name: Name to register the node under
			node_function: The async function (already decorated with @asyncflow)

		Returns:
			True if successful, False if node already exists
		"""
		if node_name in self.available_nodes:
			logger.warning("Node '%s' is already registered; skipping it", node_name)
			return False

		self.available_nodes[node_name] = node_function
		logger.debug("Registered node '%s'", node_name)
		return True

	async def expand_graph(self, new_node: str, position: Optional[int] = None) -> bool:
		"""
		Add a node to the active graph at runtime.

		The node must already be registered (either during initialization via
		_register_available_nodes() or at runtime via register_node()).

		Args:
			new_node: Name of the node to add (must exist in available_nodes)
			position: Optional position to insert node (None = append to end)

		Returns:
			True if successful, False otherwise
		"""
		logger.debug(
			"Expanding the graph with node '%s' at position %s",
			new_node,
			"end" if position is None else position,
		)

		if new_node not in self.available_nodes:
			logger.warning(
				"Cannot add node '%s': it is not registered (registered: %s)",
				new_node,
				list(self.available_nodes),
			)
			return False

		if new_node in self.active_node_names:
			logger.warning("Cannot add node '%s': it is already in the graph", new_node)
			return False

		# Add node at specified position
		if position is None:
			self.active_node_names.append(new_node)
			logger.debug("Appended '%s' to the end", new_node)
		else:
			self.active_node_names.insert(position, new_node)
			logger.debug("Inserted '%s' at position %d", new_node, position)

		await self.rebuild_graph()
		logger.debug("Expanded the graph to %s", self.active_node_names)
		return True

	async def reduce_graph(self, node_to_remove: str) -> bool:
		"""
		Remove a node from the graph at runtime.

		Args:
			node_to_remove: Name of the node to remove

		Returns:
			True if successful, False otherwise
		"""
		logger.debug("Reducing the graph by node '%s'", node_to_remove)

		if node_to_remove not in self.active_node_names:
			logger.warning(
				"Cannot remove node '%s': it is not in the graph", node_to_remove
			)
			return False

		self.active_node_names.remove(node_to_remove)
		logger.debug("Removed '%s' from the active nodes", node_to_remove)

		await self.rebuild_graph()
		logger.debug("Reduced the graph to %s", self.active_node_names)
		return True

	async def update_graph(self, old_node: str, new_node: str) -> bool:
		"""
		Replace one node with another at runtime.

		Args:
			old_node: Node to replace
			new_node: Node to replace it with

		Returns:
			True if successful, False otherwise
		"""
		logger.debug("Updating the graph: replacing '%s' with '%s'", old_node, new_node)

		if old_node not in self.active_node_names:
			logger.warning("Cannot replace node '%s': it is not in the graph", old_node)
			return False

		if new_node not in self.available_nodes:
			logger.warning(
				"Cannot replace with node '%s': it is not registered", new_node
			)
			return False

		if new_node != old_node and new_node in self.active_node_names:
			logger.warning(
				"Cannot replace with node '%s': it is already in the graph", new_node
			)
			return False

		idx = self.active_node_names.index(old_node)
		self.active_node_names[idx] = new_node
		logger.debug("Replaced '%s' with '%s' at index %d", old_node, new_node, idx)

		await self.rebuild_graph()
		logger.debug("Updated the graph to %s", self.active_node_names)
		return True

	async def run(
		self, initial_state: Optional[Dict] = None
	) -> Optional[Dict[str, Any]]:
		"""
		Execute the current graph configuration.

		Args:
			initial_state: Starting state for the graph (uses state schema defaults if None)

		Returns:
			Final state after execution
		"""
		if not self.graph:
			logger.warning("No graph to run: call rebuild_graph() first")
			return None

		logger.debug("Running the graph %s", self.active_node_names)

		# Execute graph
		result = await self.graph.ainvoke(initial_state or {})

		logger.debug("Graph run complete")
		return result
