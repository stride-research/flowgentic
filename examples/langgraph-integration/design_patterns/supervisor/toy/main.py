"""
Toy Example: Supervisor with Conditional Edges + Send API for Parallel Agent Execution

This demonstrates:
1. LLM supervisor making routing decisions via conditional edges
2. Send API to invoke multiple agents in parallel
3. Timing validation to prove parallelism works
"""

import asyncio
import time
from typing import Annotated, List, Literal
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.prebuilt import create_react_agent
from langgraph.types import Send
from radical.asyncflow import ConcurrentExecutionBackend

from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.utils.llm_providers import ChatLLMProvider

from dotenv import load_dotenv

load_dotenv()


# ==================== State Definition ====================


def add_results(left: List[str], right: List[str]) -> List[str]:
	"""Reducer for parallel results - concatenates lists."""
	return left + right


class WorkState(BaseModel):
	"""Workflow state tracking messages and agent results."""

	messages: Annotated[List[BaseMessage], add_messages] = []
	task_type: str = ""
	parallel_results: Annotated[List[str], add_results] = Field(default_factory=list)


class AgentTask(BaseModel):
	"""Task assigned to a specific agent."""

	agent_name: str
	task_description: str


# ==================== Create Workflow ====================


def create_toy_workflow(agents_manager: LangraphIntegration):
	"""Build a toy workflow demonstrating conditional edges + Send API."""

	# ==================== Simulated Work Tools ====================

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
	)
	async def analyze_data(data_type: str) -> str:
		"""Simulate data analysis - takes 2 seconds."""
		start = time.time()
		print(
			f"📊 [DATA AGENT] Starting {data_type} analysis at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}"
		)
		await asyncio.sleep(2)  # Simulate work
		elapsed = time.time() - start
		result = f"Analysis of {data_type} complete (took {elapsed:.2f}s)"
		print(
			f"✅ [DATA AGENT] Finished at {datetime.now().strftime('%H:%M:%S.%f')[:-3]} - {result}"
		)
		return result

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
	)
	async def fetch_information(topic: str) -> str:
		"""Simulate information fetching - takes 2 seconds."""
		start = time.time()
		print(
			f"🔍 [RESEARCH AGENT] Starting {topic} research at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}"
		)
		await asyncio.sleep(2)  # Simulate work
		elapsed = time.time() - start
		result = f"Research on {topic} complete (took {elapsed:.2f}s)"
		print(
			f"✅ [RESEARCH AGENT] Finished at {datetime.now().strftime('%H:%M:%S.%f')[:-3]} - {result}"
		)
		return result

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
	)
	async def process_request(request: str) -> str:
		"""Simulate request processing - takes 2 seconds."""
		start = time.time()
		print(
			f"⚙️ [PROCESSING AGENT] Starting {request} at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}"
		)
		await asyncio.sleep(2)  # Simulate work
		elapsed = time.time() - start
		result = f"Processing of {request} complete (took {elapsed:.2f}s)"
		print(
			f"✅ [PROCESSING AGENT] Finished at {datetime.now().strftime('%H:%M:%S.%f')[:-3]} - {result}"
		)
		return result

	# ==================== Supervisor Agent ====================

	supervisor_agent = create_react_agent(
		model=ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash"),
		tools=[],
		prompt=(
			"You are a Supervisor Agent that routes tasks to specialized agents.\n\n"
			"Based on the user's request, you must decide which agents to invoke:\n"
			"- For 'analyze' requests: Return 'parallel_route'\n"
			"- For 'simple' requests: Return 'single_route'\n"
			"- For anything else: Return 'end'\n\n"
			"YOUR RESPONSE MUST BE EXACTLY ONE OF: parallel_route, single_route, or end\n"
			"Do NOT add any other text, just respond with the route name."
		),
		name="supervisor",
	)

	# ==================== Worker Agents ====================

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.EXECUTION_BLOCK
	)
	async def data_agent(state: WorkState) -> WorkState:
		"""Data analysis agent - uses analyze_data tool."""
		print("\n🤖 DATA AGENT ACTIVATED")

		agent = create_react_agent(
			model=ChatLLMProvider(
				provider="OpenRouter", model="google/gemini-2.5-flash"
			),
			tools=[analyze_data],
			name="data_agent",
			prompt="You are a data analyst. Use the analyze_data tool to analyze 'financial_metrics'.",
		)

		result = await agent.ainvoke(state)
		return {
			"messages": result["messages"],
			"parallel_results": ["Data agent completed"],
		}

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.EXECUTION_BLOCK
	)
	async def research_agent(state: WorkState) -> WorkState:
		"""Research agent - uses fetch_information tool."""
		print("\n🤖 RESEARCH AGENT ACTIVATED")

		agent = create_react_agent(
			model=ChatLLMProvider(
				provider="OpenRouter", model="google/gemini-2.5-flash"
			),
			tools=[fetch_information],
			name="research_agent",
			prompt="You are a researcher. Use the fetch_information tool to research 'market_trends'.",
		)

		result = await agent.ainvoke(state)
		return {
			"messages": result["messages"],
			"parallel_results": ["Research agent completed"],
		}

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.EXECUTION_BLOCK
	)
	async def processing_agent(state: WorkState) -> WorkState:
		"""Processing agent - uses process_request tool."""
		print("\n🤖 PROCESSING AGENT ACTIVATED")

		agent = create_react_agent(
			model=ChatLLMProvider(
				provider="OpenRouter", model="google/gemini-2.5-flash"
			),
			tools=[process_request],
			name="processing_agent",
			prompt="You are a processor. Use the process_request tool to process 'user_data'.",
		)

		result = await agent.ainvoke(state)
		return {
			"messages": result["messages"],
			"parallel_results": ["Processing agent completed"],
		}

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.EXECUTION_BLOCK
	)
	async def single_agent(state: WorkState) -> WorkState:
		"""Simple single agent for non-parallel tasks."""
		print("\n🤖 SINGLE AGENT ACTIVATED")

		agent = create_react_agent(
			model=ChatLLMProvider(
				provider="OpenRouter", model="google/gemini-2.5-flash"
			),
			tools=[analyze_data],
			name="single_agent",
			prompt="You are a simple agent. Use the analyze_data tool to analyze 'simple_task'.",
		)

		result = await agent.ainvoke(state)
		return {"messages": result["messages"]}

	# ==================== Supervisor Routing Logic with Send API ====================

	def supervisor_router(state: WorkState):
		"""
		Route based on supervisor's decision - CONDITIONAL EDGE.
		Returns either:
		- A list of Send objects (for parallel execution)
		- A string node name (for single node)
		- END constant (to finish)
		"""
		messages = state.messages
		last_message = messages[-1]

		# Extract supervisor's routing decision
		content = last_message.content.strip().lower()

		print(f"\n🧭 SUPERVISOR DECISION: '{content}'")

		if "parallel" in content:
			print("   → Routing to PARALLEL EXECUTION (Send API)")
			print(f"   🚀 Fanning out to 3 agents IN PARALLEL")
			print(f"   Start time: {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
			# Return list of Send objects for parallel execution
			return [
				Send("data_agent", state),
				Send("research_agent", state),
				Send("processing_agent", state),
			]
		elif "single" in content:
			print("   → Routing to SINGLE AGENT")
			return "single_agent"
		else:
			print("   → Routing to END")
			return END

	# ==================== Aggregator Node ====================

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.EXECUTION_BLOCK
	)
	async def aggregator(state: WorkState) -> WorkState:
		"""Collect results from parallel agents."""
		print("\n📦 AGGREGATOR: Collecting parallel results")
		print(f"   Results: {state.parallel_results}")

		summary = HumanMessage(
			content=f"Parallel execution complete! Collected {len(state.parallel_results)} results."
		)
		return {"messages": [summary]}

	# ==================== Build Graph ====================

	# Register nodes for introspection
	agents_manager.agent_introspector._all_nodes = [
		"supervisor",
		"data_agent",
		"research_agent",
		"processing_agent",
		"single_agent",
		"aggregator",
	]

	# Wrap worker nodes with introspection (NOT the supervisor - it's a CompiledStateGraph)
	data_wrapped = agents_manager.agent_introspector.introspect_node(
		data_agent, node_name="data_agent"
	)
	research_wrapped = agents_manager.agent_introspector.introspect_node(
		research_agent, node_name="research_agent"
	)
	processing_wrapped = agents_manager.agent_introspector.introspect_node(
		processing_agent, node_name="processing_agent"
	)
	single_wrapped = agents_manager.agent_introspector.introspect_node(
		single_agent, node_name="single_agent"
	)
	aggregator_wrapped = agents_manager.agent_introspector.introspect_node(
		aggregator, node_name="aggregator"
	)

	workflow = StateGraph(WorkState)

	# Add all nodes (supervisor is not wrapped)
	workflow.add_node("supervisor", supervisor_agent)
	workflow.add_node("data_agent", data_wrapped)
	workflow.add_node("research_agent", research_wrapped)
	workflow.add_node("processing_agent", processing_wrapped)
	workflow.add_node("single_agent", single_wrapped)
	workflow.add_node("aggregator", aggregator_wrapped)

	# Entry point
	workflow.add_edge(START, "supervisor")

	# CONDITIONAL EDGE from supervisor
	# The router itself returns either Send objects (list), node name (str), or END
	workflow.add_conditional_edges(
		"supervisor",
		supervisor_router,
	)

	# Parallel agents go to aggregator
	workflow.add_edge("data_agent", "aggregator")
	workflow.add_edge("research_agent", "aggregator")
	workflow.add_edge("processing_agent", "aggregator")

	# Single agent and aggregator go to end
	workflow.add_edge("single_agent", END)
	workflow.add_edge("aggregator", END)

	return workflow


# ==================== Main Execution ====================


async def run_parallel_demo():
	"""Demonstrate parallel execution with timing validation."""
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		print("=" * 80)
		print("🎯 TOY EXAMPLE: Conditional Edges + Send API for Parallel Execution")
		print("=" * 80)

		workflow = create_toy_workflow(agents_manager)
		app = workflow.compile()

		# ==================== TEST 1: Parallel Route ====================
		print("\n" + "=" * 80)
		print("TEST 1: PARALLEL EXECUTION (3 agents in parallel)")
		print("=" * 80)

		parallel_start = time.time()

		result = await app.ainvoke(
			{
				"messages": [
					HumanMessage(
						content="I need to analyze complex data - use parallel processing"
					)
				],
				"task_type": "parallel",
			}
		)

		parallel_elapsed = time.time() - parallel_start

		print("\n" + "=" * 80)
		print(f"✅ PARALLEL TEST COMPLETE")
		print(f"⏱️  Total time: {parallel_elapsed:.2f} seconds")
		print(f"📊 Expected: ~2 seconds (all run in parallel)")
		print(f"📊 If sequential: would take ~6 seconds (2s × 3 agents)")
		print(f"🎉 Speedup: {6 / parallel_elapsed:.1f}x faster than sequential!")
		print("=" * 80)

		# ==================== TEST 2: Single Route ====================
		print("\n" + "=" * 80)
		print("TEST 2: SINGLE EXECUTION (1 agent)")
		print("=" * 80)

		single_start = time.time()

		result = await app.ainvoke(
			{
				"messages": [
					HumanMessage(content="Simple task - no need for parallelism")
				],
				"task_type": "single",
			}
		)

		single_elapsed = time.time() - single_start

		print("\n" + "=" * 80)
		print(f"✅ SINGLE TEST COMPLETE")
		print(f"⏱️  Total time: {single_elapsed:.2f} seconds")
		print("=" * 80)

		# ==================== Generate Telemetry ====================
		import pathlib

		current_directory = str(pathlib.Path(__file__).parent.resolve())
		agents_manager.utils.create_output_results_dirs(current_directory)
		agents_manager.agent_introspector.generate_report(
			dir_to_write=current_directory
		)
		await agents_manager.utils.render_graph(app, dir_to_write=current_directory)

		print("\n📊 Execution report and graph generated!")
		print(f"📁 Output directory: {current_directory}/agent_execution_results/")


if __name__ == "__main__":
	asyncio.run(run_parallel_demo())
