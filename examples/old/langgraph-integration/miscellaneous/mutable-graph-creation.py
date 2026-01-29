"""
Dynamic Runtime Graph Creation with Flowgentic Integration

This example demonstrates how to dynamically modify a LangGraph at runtime:
- Expand: Add new nodes to the graph
- Reduce: Remove nodes from the graph
- Update: Replace nodes with different implementations

All nodes use flowgentic's AsyncFlow integration with proper async execution,
fault tolerance, and comprehensive logging.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, List, Optional, TypedDict
from dotenv import load_dotenv
from radical.asyncflow import ConcurrentExecutionBackend
from langgraph.graph.message import add_messages

from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.mutable_graph import MutableGraph
from flowgentic.utils.logger.logger import Logger

load_dotenv()

# Initialize flowgentic logger
Logger(colorful_output=True, logger_level="INFO")
logger = logging.getLogger(__name__)


# Define state schema
class GraphState(TypedDict):
	"""State schema for the dynamic graph"""

	messages: Annotated[List[str], add_messages]
	execution_path: List[str]
	operation_count: int


class DynamicFlowgenticGraph(MutableGraph):
	"""
	Example implementation of MutableGraph with sequential data processing nodes.

	Demonstrates:
	- Node registration with AsyncFlow decoration
	- Sequential edge topology (default from base class)
	- State tracking through execution
	- Custom initial active nodes
	- Adding new nodes at runtime with register_node()
	"""

	def __init__(
		self, agents_manager: LangraphIntegration, initial_active_nodes: List[str]
	):
		"""
		Initialize the graph.

		Args:
			agents_manager: LangraphIntegration for AsyncFlow execution
			initial_active_nodes: Which nodes to start with (required)
		"""
		super().__init__(agents_manager, initial_active_nodes)

	def _register_available_nodes(self):
		"""
		Register all available node functions using flowgentic's AsyncFlow wrappers.
		Each node is decorated to run as an async function task.
		"""
		logger.info("📝 Registering available node functions with AsyncFlow")

		# Define node functions with AsyncFlow decoration
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def node_a(state: GraphState) -> GraphState:
			"""Process node A - Data initialization"""
			logger.info("🔷 Node A: Initializing data processing")
			await asyncio.sleep(0.1)  # Simulate async work
			return {
				"messages": ["[Node A] Data initialized"],
				"execution_path": state.get("execution_path", []) + ["node_a"],
				"operation_count": state.get("operation_count", 0) + 1,
			}

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def node_b(state: GraphState) -> GraphState:
			"""Process node B - Data transformation"""
			logger.info("🔶 Node B: Transforming data")
			await asyncio.sleep(0.1)  # Simulate async work
			return {
				"messages": ["[Node B] Data transformed"],
				"execution_path": state.get("execution_path", []) + ["node_b"],
				"operation_count": state.get("operation_count", 0) + 1,
			}

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def node_c(state: GraphState) -> GraphState:
			"""Process node C - Data validation"""
			logger.info("🔷 Node C: Validating data")
			await asyncio.sleep(0.1)  # Simulate async work
			return {
				"messages": ["[Node C] Data validated"],
				"execution_path": state.get("execution_path", []) + ["node_c"],
				"operation_count": state.get("operation_count", 0) + 1,
			}

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def node_d(state: GraphState) -> GraphState:
			"""Process node D - Data aggregation"""
			logger.info("🔶 Node D: Aggregating results")
			await asyncio.sleep(0.1)  # Simulate async work
			return {
				"messages": ["[Node D] Data aggregated"],
				"execution_path": state.get("execution_path", []) + ["node_d"],
				"operation_count": state.get("operation_count", 0) + 1,
			}

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def node_e(state: GraphState) -> GraphState:
			"""Process node E - Data enrichment"""
			logger.info("🔷 Node E: Enriching data")
			await asyncio.sleep(0.1)  # Simulate async work
			return {
				"messages": ["[Node E] Data enriched"],
				"execution_path": state.get("execution_path", []) + ["node_e"],
				"operation_count": state.get("operation_count", 0) + 1,
			}

		# Store all available nodes
		self.available_nodes = {
			"node_a": node_a,
			"node_b": node_b,
			"node_c": node_c,
			"node_d": node_d,
			"node_e": node_e,
		}

		logger.info(
			f"✅ Registered {len(self.available_nodes)} node functions: {list(self.available_nodes.keys())}"
		)

	def _get_state_schema(self) -> type:
		"""Return the state schema for this graph."""
		return GraphState

	async def run(self, initial_state: Optional[GraphState] = None) -> GraphState:
		"""
		Execute the current graph configuration with custom logging.

		Overrides base class to add detailed result logging.

		Args:
			initial_state: Starting state for the graph

		Returns:
			Final state after execution
		"""
		if initial_state is None:
			initial_state = {"messages": [], "execution_path": [], "operation_count": 0}

		# Call parent's run method
		result = await super().run(initial_state)

		# Add detailed logging specific to this graph
		if result:
			logger.info(f"  📊 Operations: {result['operation_count']}")
			logger.info(f"  🛤️  Path: {' -> '.join(result['execution_path'])}")
			logger.info(f"  💬 Messages: {len(result['messages'])}")

		return result


class ConditionalFlowGraph(MutableGraph):
	"""
	Example showing custom edge logic with conditional routing.

	Demonstrates how to override _add_edges() for non-sequential flows.
	"""

	def __init__(
		self, agents_manager: LangraphIntegration, initial_active_nodes: List[str]
	):
		"""
		Initialize conditional flow graph.

		Args:
			agents_manager: LangraphIntegration for AsyncFlow execution
			initial_active_nodes: Which nodes to start with (required)
		"""
		super().__init__(agents_manager, initial_active_nodes)

	def _register_available_nodes(self) -> None:
		"""Register nodes with conditional logic."""
		logger.info("📝 Registering conditional flow nodes")

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def decision_node(state: GraphState) -> GraphState:
			"""Decision node that sets routing information"""
			logger.info("🔀 Decision Node: Evaluating routing")
			await asyncio.sleep(0.1)

			# Decide which path to take based on operation count
			operation_count = state.get("operation_count", 0) + 1
			should_optimize = operation_count % 2 == 0

			return {
				"messages": [
					f"[Decision] Route to {'optimization' if should_optimize else 'standard'}"
				],
				"execution_path": state.get("execution_path", []) + ["decision"],
				"operation_count": operation_count,
				"should_optimize": should_optimize,  # Add routing flag
			}

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def standard_node(state: GraphState) -> GraphState:
			"""Standard processing path"""
			logger.info("📊 Standard Node: Standard processing")
			await asyncio.sleep(0.1)
			return {
				"messages": ["[Standard] Standard processing"],
				"execution_path": state.get("execution_path", []) + ["standard"],
				"operation_count": state.get("operation_count", 0) + 1,
			}

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def optimize_node(state: GraphState) -> GraphState:
			"""Optimized processing path"""
			logger.info("⚡ Optimize Node: Optimized processing")
			await asyncio.sleep(0.1)
			return {
				"messages": ["[Optimize] Optimized processing"],
				"execution_path": state.get("execution_path", []) + ["optimize"],
				"operation_count": state.get("operation_count", 0) + 1,
			}

		self.available_nodes = {
			"decision": decision_node,
			"standard": standard_node,
			"optimize": optimize_node,
		}

		logger.info(f"✅ Registered {len(self.available_nodes)} conditional nodes")

	def _get_state_schema(self) -> type:
		"""Return state schema (extends GraphState with should_optimize flag)"""
		return GraphState

	def _add_edges(self, workflow):
		"""
		Custom edge logic with conditional routing.

		Overrides base class to implement conditional edges instead of sequential.
		"""
		from langgraph.graph import END

		# Helper function for routing
		def route_after_decision(state: GraphState) -> str:
			"""Route based on state evaluation"""
			if state.get("should_optimize", False):
				return "optimize"
			return "standard"

		# Add conditional edge from decision node
		if "decision" in self.active_node_names:
			workflow.add_conditional_edges("decision", route_after_decision)
			logger.debug(f"  🔀 decision -> [conditional: standard|optimize]")

		# Both paths lead to END
		if "standard" in self.active_node_names:
			workflow.add_edge("standard", END)
			logger.debug(f"  🏁 standard -> END")

		if "optimize" in self.active_node_names:
			workflow.add_edge("optimize", END)
			logger.debug(f"  🏁 optimize -> END")


async def demonstrate_runtime_graph_creation():
	"""
	Demonstration of runtime graph creation with custom initial nodes.
	"""
	logger.info("=" * 80)
	logger.info("🎯 DYNAMIC RUNTIME GRAPH CREATION DEMONSTRATION")
	logger.info("=" * 80)

	# Initialize flowgentic backend
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor(max_workers=4))

	async with LangraphIntegration(backend=backend) as agents_manager:
		# PART 1: Sequential graph with custom initial nodes
		logger.info("\n" + "=" * 80)
		logger.info("PART 1: Sequential Graph with Custom Initial Nodes")
		logger.info("=" * 80)
		logger.info("Creating graph with only nodes A and B active initially")

		dg = DynamicFlowgenticGraph(
			agents_manager, initial_active_nodes=["node_a", "node_b"]
		)
		await dg.rebuild_graph()
		result = await dg.run()
		_print_result(result, "Initial: A -> B")

		# EXPAND: Add existing node C
		logger.info("\n--- EXPAND: Add Node C (pre-registered) ---")
		await dg.expand_graph("node_c")
		result = await dg.run()
		_print_result(result, "After: A -> B -> C")

		# REDUCE: Remove node B
		logger.info("\n--- REDUCE: Remove Node B ---")
		await dg.reduce_graph("node_b")
		result = await dg.run()
		_print_result(result, "After: A -> C")

		# UPDATE: Replace C with D
		logger.info("\n--- UPDATE: Replace C with D ---")
		await dg.update_graph("node_c", "node_d")
		result = await dg.run()
		_print_result(result, "After: A -> D")

		# REGISTER + EXPAND: Add completely new node at runtime
		logger.info("\n--- REGISTER NEW NODE: Add node_f (not pre-registered) ---")

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def node_f(state: GraphState) -> GraphState:
			"""New node F - Created at runtime"""
			logger.info("🆕 Node F: Runtime-registered node")
			await asyncio.sleep(0.1)
			return {
				"messages": ["[Node F] Runtime node executed"],
				"execution_path": state.get("execution_path", []) + ["node_f"],
				"operation_count": state.get("operation_count", 0) + 1,
			}

		# Register the new node
		dg.register_node("node_f", node_f)

		# Add it to the graph
		await dg.expand_graph("node_f", position=1)
		result = await dg.run()
		_print_result(result, "After: A -> F (new) -> D")

		logger.info("\n" + "=" * 80)
		logger.info("✅ PART 1 COMPLETE")
		logger.info(f"📋 Final Configuration: {' -> '.join(dg.active_node_names)}")
		logger.info(
			f"📊 Total Available Nodes: {len(dg.available_nodes)} (5 initial + 1 runtime)"
		)
		logger.info("=" * 80)

		# PART 2: Conditional flow graph
		logger.info("\n" + "=" * 80)
		logger.info("PART 2: Conditional Flow Graph (Non-Sequential Edges)")
		logger.info("=" * 80)

		cg = ConditionalFlowGraph(
			agents_manager, initial_active_nodes=["decision", "standard", "optimize"]
		)
		await cg.rebuild_graph()

		# Run twice to demonstrate different routing
		logger.info("\n--- First Run (should route to standard) ---")
		result1 = await cg.run(
			{"messages": [], "execution_path": [], "operation_count": 0}
		)
		print(f"\n🔀 Conditional Result 1: {' → '.join(result1['execution_path'])}")

		logger.info("\n--- Second Run (should route to optimize) ---")
		result2 = await cg.run(
			{"messages": [], "execution_path": [], "operation_count": 1}
		)
		print(f"🔀 Conditional Result 2: {' → '.join(result2['execution_path'])}")

		logger.info("\n" + "=" * 80)
		logger.info("🎊 ALL DEMONSTRATIONS COMPLETE")
		logger.info("=" * 80)
		logger.info("✅ Sequential graphs with EXPAND/REDUCE/UPDATE")
		logger.info("✅ Custom initial active nodes")
		logger.info("✅ Runtime node registration with register_node()")
		logger.info("✅ Conditional routing with custom _add_edges()")
		logger.info("=" * 80)


def _print_result(result: GraphState, phase: str):
	"""Helper to print execution results in a readable format"""
	print("\n" + "-" * 60)
	print(f"📈 RESULT: {phase}")
	print("-" * 60)
	print(f"Execution Path: {' → '.join(result['execution_path'])}")
	print(f"Operations:     {result['operation_count']}")
	print(f"Messages:       {result['messages']}")
	print("-" * 60)


if __name__ == "__main__":
	asyncio.run(demonstrate_runtime_graph_creation(), debug=True)
