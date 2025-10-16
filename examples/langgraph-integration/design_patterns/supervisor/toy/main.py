import asyncio
from concurrent.futures import ThreadPoolExecutor
import pathlib
from typing import Annotated, Dict, List, Optional
import logging
import time
import operator

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field
from radical.asyncflow import ConcurrentExecutionBackend

from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.utils.supervisor import create_llm_router, supervisor_fan_out
from flowgentic.utils.llm_providers import ChatLLMProvider

# Load environment variables from .env file
load_dotenv()


class GraphState(BaseModel):
	query: str = Field(..., description="User query to route")
	routing_decision: Optional[List[str]] = Field(
		default=None, description="List of agents to route to"
	)
	results: Annotated[Dict[str, str], operator.or_] = Field(
		default_factory=dict, description="Merge dicts from parallel agents"
	)
	final_summary: Optional[str] = Field(
		default=None, description="Combined summary from gather node"
	)
	messages: Annotated[List[BaseMessage], add_messages] = []


async def main():
	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s.%(msecs)03d %(threadName)s %(levelname)s: %(message)s",
		datefmt="%H:%M:%S",
	)

	graph = StateGraph(GraphState)
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		# Define the routing prompt template
		routing_prompt_template = """
Based on the user's query, decide which agent(s) should handle it:

- agent_A: Handles data processing and analysis tasks
- agent_B: Handles question answering and information retrieval
- both: When the query requires both processing AND answering

User query: "{query}"

Respond with a list of the agents to call: 
For example for agent_A: ["agent_A"]
For both: ["agent_A, "agent_B"]
"""

		# Define the model for routing
		router_model = ChatLLMProvider(
			provider="OpenRouter", model="google/gemini-2.5-flash"
		)

		# Create and decorate the router function using the factory
		llm_router = agents_manager.execution_wrappers.asyncflow(
			create_llm_router(routing_prompt_template, router_model),
			flow_type=AsyncFlowType.EXECUTION_BLOCK,
		)

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def agent_a(state: GraphState) -> dict:
			start = time.perf_counter()
			logging.info(f"📊 agent_a START - Processing data for: '{state.query}'")
			await asyncio.sleep(3.0)  # simulate work
			elapsed = (time.perf_counter() - start) * 1000
			logging.info(f"📊 agent_a END   took_ms={elapsed:.1f}")
			return {"results": {"agent_A": f"Data analysis complete: {state.query}"}}

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def agent_b(state: GraphState) -> dict:
			start = time.perf_counter()
			logging.info(f"💬 agent_b START - Answering: '{state.query}'")
			await asyncio.sleep(3.0)  # simulate work
			elapsed = (time.perf_counter() - start) * 1000
			logging.info(f"💬 agent_b END   took_ms={elapsed:.1f}")
			return {
				"results": {
					"agent_b": f"Answer: {state.query} - Parallelism is executing multiple tasks simultaneously!"
				}
			}

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def gather(state: GraphState) -> dict:
			"""Gather and synthesize results from all executed agents using LLM."""
			logging.info(f"🔄 Gather: Combining results from agents...")

			results = state.results

			# If only one agent ran, just pass through
			if len(results) == 1:
				agent_name = list(results.keys())[0]
				final_summary = (
					f"[Single Agent Result - {agent_name}]\n{results[agent_name]}"
				)
				logging.info(f"✅ Gather: Single agent result passed through")
				return {"final_summary": final_summary}

			# If both agents ran, use LLM to synthesize their outputs
			synthesis_agent = create_react_agent(
				model=ChatLLMProvider(
					provider="OpenRouter", model="google/gemini-2.5-flash"
				),
				tools=[],
			)

			synthesis_prompt = f"""
You are a synthesis agent. Combine the following outputs from two parallel agents into a coherent, comprehensive response.

Agent A (Data Processing): {results.get("agent_A", "N/A")}

Agent B (Question Answering): {results.get("agent_B", "N/A")}

Original Query: {state.query}

Create a brief, unified response that integrates both perspectives. Keep it concise (2-3 sentences).
"""

			synthesis_result = await synthesis_agent.ainvoke(
				{
					"messages": [
						SystemMessage(
							content="You are a synthesis agent that combines multiple agent outputs into a coherent response."
						),
						HumanMessage(content=synthesis_prompt),
					]
				}
			)

			state.messages.extend(synthesis_result["messages"])

			final_summary = synthesis_result["messages"][-1].content.strip()

			logging.info(f"✅ Gather: Synthesized {len(results)} agent outputs")
			state.final_summary = final_summary
			return state

		# Nodes
		llm_router_introspection = agents_manager.agent_introspector.introspect_node(
			llm_router, "llm_router"
		)
		agent_a_introspection = agents_manager.agent_introspector.introspect_node(
			agent_a, "agent_A"
		)
		agent_b_introspection = agents_manager.agent_introspector.introspect_node(
			agent_b, "agent_B"
		)
		gather_introspection = agents_manager.agent_introspector.introspect_node(
			gather, "gather"
		)

		agents_manager.agent_introspector._all_nodes = [
			"llm_router",
			"agent_A",
			"agent_B",
			"gather",
		]

		graph.add_node("llm_router", llm_router_introspection)
		graph.add_node("agent_A", agent_a_introspection)
		graph.add_node("agent_B", agent_b_introspection)
		graph.add_node("gather", gather_introspection)

		# Edges
		graph.add_edge(START, "llm_router")
		graph.add_conditional_edges("llm_router", supervisor_fan_out)
		graph.add_edge("agent_A", "gather")
		graph.add_edge("agent_B", "gather")
		graph.add_edge("gather", END)

		app = graph.compile()

		# Test different routing scenarios
		test_queries = [
			"Analyze the data and explain what parallelism means",
		]

		for query in test_queries:
			print("\n" + "=" * 80)
			logging.info(f"🚀 Testing query: '{query}'")
			print("=" * 80)

			wall_start = time.perf_counter()
			try:
				result = await app.ainvoke(GraphState(query=query))
				wall_ms = (time.perf_counter() - wall_start) * 1000
			except Exception as e:
				print(f"❌ Workflow execution failed: {str(e)}")
				raise
			finally:
				current_directory = str(pathlib.Path(__file__).parent.resolve())
				agents_manager.utils.create_output_results_dirs(current_directory)

				agents_manager.agent_introspector.generate_report(
					dir_to_write=current_directory
				)
				await agents_manager.utils.render_graph(
					app, dir_to_write=current_directory
				)

			print(f"\n📋 Results for: '{query}'")
			print(f"   Routing: {result['routing_decision']}")
			print(f"   Agent Outputs: {result['results']}")
			print(f"\n   💡 Final Summary:\n   {result.get('final_summary', 'N/A')}")
			logging.info(f"⏱️  WALL elapsed_ms={wall_ms:.1f}")


if __name__ == "__main__":
	asyncio.run(main(), debug=True)
