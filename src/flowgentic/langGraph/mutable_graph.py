"""
Dynamic Graph Base Class for Runtime Graph Modification

Provides a reusable base class for creating LangGraphs that can be modified
at runtime with expand, reduce, and update operations.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Callable, TypedDict
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
		logger.info("🚀 Initializing MutableGraph")
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
		logger.info(
			f"✅ Activated {len(self.active_node_names)} nodes: {self.active_node_names}"
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
		logger.info(f"🔄 Rebuilding graph with nodes: {self.active_node_names}")

		if not self.active_node_names:
			logger.warning("⚠️  No active nodes to build graph")
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
		logger.info(
			f"✅ Graph rebuilt successfully with {len(self.active_node_names)} nodes"
		)

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
				logger.debug(f"  ➕ Added node: {node_name}")
			else:
				logger.error(f"❌ Node {node_name} not found in available nodes")

		# Set entry point to first node
		if self.active_node_names:
			workflow.set_entry_point(self.active_node_names[0])
			logger.debug(f"  🚪 Entry point: {self.active_node_names[0]}")

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
				logger.debug(f"  🏁 {node_name} -> END")
			else:
				# Connect to next node
				next_node = self.active_node_names[i + 1]
				workflow.add_edge(node_name, next_node)
				logger.debug(f"  ➡️  {node_name} -> {next_node}")

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
			logger.warning(f"⚠️  Node '{node_name}' already registered, skipping")
			return False

		self.available_nodes[node_name] = node_function
		logger.info(f"✅ Registered new node: '{node_name}'")
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
		logger.info(
			f"➕ EXPAND: Adding node '{new_node}' at position "
			f"{position if position is not None else 'end'}"
		)

		if new_node not in self.available_nodes:
			logger.error(
				f"❌ Node '{new_node}' not available. "
				f"Available: {list(self.available_nodes.keys())}"
			)
			return False

		if new_node in self.active_node_names:
			logger.warning(f"⚠️  Node '{new_node}' already in graph")
			return False

		# Add node at specified position
		if position is None:
			self.active_node_names.append(new_node)
			logger.debug(f"  Appended '{new_node}' to end")
		else:
			self.active_node_names.insert(position, new_node)
			logger.debug(f"  Inserted '{new_node}' at position {position}")

		await self.rebuild_graph()
		logger.info(f"✅ EXPAND Complete: {self.active_node_names}")
		return True

	async def reduce_graph(self, node_to_remove: str) -> bool:
		"""
		Remove a node from the graph at runtime.

		Args:
			node_to_remove: Name of the node to remove

		Returns:
			True if successful, False otherwise
		"""
		logger.info(f"➖ REDUCE: Removing node '{node_to_remove}'")

		if node_to_remove not in self.active_node_names:
			logger.error(f"❌ Node '{node_to_remove}' not in active graph")
			return False

		self.active_node_names.remove(node_to_remove)
		logger.debug(f"  Removed '{node_to_remove}' from active nodes")

		await self.rebuild_graph()
		logger.info(f"✅ REDUCE Complete: {self.active_node_names}")
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
		logger.info(f"🔄 UPDATE: Replacing '{old_node}' with '{new_node}'")

		if old_node not in self.active_node_names:
			logger.error(f"❌ Node '{old_node}' not in active graph")
			return False

		if new_node not in self.available_nodes:
			logger.error(f"❌ Node '{new_node}' not available")
			return False

		idx = self.active_node_names.index(old_node)
		self.active_node_names[idx] = new_node
		logger.debug(f"  Replaced '{old_node}' with '{new_node}' at index {idx}")

		await self.rebuild_graph()
		logger.info(f"✅ UPDATE Complete: {self.active_node_names}")
		return True

	async def run(self, initial_state: Optional[Dict] = None):
		"""
		Execute the current graph configuration.

		Args:
			initial_state: Starting state for the graph (uses state schema defaults if None)

		Returns:
			Final state after execution
		"""
		if not self.graph:
			logger.error("❌ No graph to run - call rebuild_graph() first")
			return None

		logger.info(f"▶️  Executing graph: {' -> '.join(self.active_node_names)}")

		# Execute graph
		result = await self.graph.ainvoke(initial_state or {})

		logger.info(f"✅ Execution complete!")
		return result
