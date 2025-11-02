import sys
from typing import Annotated, Any, Dict, List, Optional
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph, add_messages
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel
from radical.asyncflow import ConcurrentExecutionBackend
from concurrent.futures import ThreadPoolExecutor
from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.langGraph.main import LangraphIntegration
import asyncio
from langgraph.checkpoint.memory import InMemorySaver
from flowgentic.utils.telemetry import GraphIntrospector
from flowgentic.utils.llm_providers import ChatLLMProvider
import logging
from dotenv import load_dotenv
import pickle

load_dotenv()

logger = logging.getLogger(__name__)


class WorkflowState(BaseModel):
	messages: Annotated[List[BaseMessage], add_messages] = []
	synthesis_agent_output: Optional[str] = None


async def start_app():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:

		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION)
		async def document_generator_tool(document_content: Dict[str, Any]) -> str:
			"""Generate a formatted document from analysis results."""
			print(f"Document generatio ntool is being executed...")
			await asyncio.sleep(0.3)
			key_points = document_content.get("key_points", [])
			return f"Executive Summary: Succesfully generated comprehensive report covering {len(key_points)} critical insights"

		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.EXECUTION_BLOCK)
		async def _synthesis_agent_node(state: WorkflowState) -> WorkflowState:
			"""Synthesis agent execution node."""
			print("🏗️ Synthesis Agent Node: Creating final deliverables...")

			try:
				start_time = asyncio.get_event_loop().time()

				tools = [document_generator_tool]

				synthesis_agent = create_react_agent(
					model=ChatLLMProvider(
						provider="OpenRouter", model="google/gemini-2.5-pro"
					),
					tools=tools,
				)

				synthesis_input = f"""
Based on the research findings:'Research findings indicate that consumers are increasingly prioritizing sustainability in their purchasing decisions. Our study, which surveyed 500 individuals across various demographics, found that **75% of respondents are willing to pay a premium for eco-friendly products**. This trend is particularly strong among younger generations, with **85% of Gen Z and millennials** stating that a company's environmental practices influence their brand loyalty. 

We also observed a significant shift in consumer behavior toward a preference for transparent supply chains. Participants expressed a desire for clear labeling and accessible information regarding product sourcing and manufacturing processes. These findings suggest that brands that effectively communicate their sustainability efforts and demonstrate corporate responsibility are likely to gain a competitive advantage in the current market.'

Please create a comprehensive synthesis with clear recommendations for a clean energy startup focusing on renewable energy storage technologies. Create a document for this synthesis. 
You must use the tools provided to you. If you cant use the given tools explain why
"""

				synthesis_state = {
					"messages": [
						SystemMessage(
							content="You are a synthesis agent specializing in creating comprehensive reports and deliverables. Your job is to take research findings and create polished, actionable documents with clear recommendations. Every time you want to invoke tool, explain your planning planning strategy beforehand"
						),
						HumanMessage(content=synthesis_input),
					]
				}
				synthesis_result = await synthesis_agent.ainvoke(synthesis_state)
				execution_time = asyncio.get_event_loop().time() - start_time
				state.messages.extend(synthesis_result.get("messages"))
				logger.debug(
					f"SYNTHESIS RESULTS MESSAGES: {synthesis_result.get('messages')}"
				)
				state.synthesis_agent_output = synthesis_result

				print(f"Snytheis agent output: {synthesis_result}")

				print(f"✅ Synthesis Agent complete in {execution_time:.2f}s")

			except Exception as e:
				error_msg = f"Synthesis agent error: {str(e)}"
				logger.error(error_msg)

			return state

		# Creating the graph
		workflow = StateGraph(WorkflowState)

		node_function = agents_manager.agent_introspector.introspect_node(
			_synthesis_agent_node, node_name="_synthesis_agent_node"
		)
		agents_manager.agent_introspector._all_nodes = ["_synthesis_agent_node"]

		workflow.add_node("synthetizer_agent", node_function)
		workflow.set_entry_point("synthetizer_agent")
		workflow.add_edge("synthetizer_agent", END)

		# Compile the app
		memory = InMemorySaver()
		app = workflow.compile(checkpointer=memory)

		print("🚀 Starting Sequential Agent Workflow")
		print("=" * 60)

		try:
			# Execute workflow
			config = {"configurable": {"thread_id": "1"}}
			async for chunk in app.astream({}, config=config, stream_mode="values"):
				print(f"Chunk: {chunk}\n")

		except Exception as e:
			raise
		finally:
			agents_manager.agent_introspector.generate_report()
			await agents_manager.utils.render_graph(app)


if __name__ == "__main__":
	asyncio.run(start_app())


"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Dict, TypedDict
import logging
import time
import operator

from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from radical.asyncflow import ConcurrentExecutionBackend

from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.langGraph.main import LangraphIntegration


class GraphState(TypedDict, total=False):
    query: str
    results: Annotated[Dict[str, str], operator.or_]  # Merge dicts from parallel agents




async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(threadName)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    graph = StateGraph(GraphState)
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

    async with LangraphIntegration(backend=backend) as agents_manager:


        async def fan_out(state: GraphState):
            # Returning two Sends triggers parallel execution of both nodes.
            return [Send("agent_a", state), Send("agent_b", state)]


        @agents_manager.execution_wrappers.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)
        async def router(state: GraphState) -> GraphState:
            # No-op node used to branch to parallel agents exactly once
            return state

        @agents_manager.execution_wrappers.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)
        async def agent_a(state: GraphState) -> GraphState:
            start = time.perf_counter()
            logging.info(f"agent_a START query='{state['query']}'")
            time.sleep(2.0)  # simulate work
            elapsed = (time.perf_counter() - start) * 1000
            logging.info(f"agent_a END   took_ms={elapsed:.1f}")
            return {"results": {"agent_a": f"A processed: {state['query']}"}}


        @agents_manager.execution_wrappers.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)
        async def agent_b(state: GraphState) -> GraphState:
            start = time.perf_counter()
            logging.info(f"agent_b START query='{state['query']}'")
            time.sleep(2.0)  # simulate work (same duration to demonstrate overlap)
            elapsed = (time.perf_counter() - start) * 1000
            logging.info(f"agent_b END   took_ms={elapsed:.1f}")
            return {"results": {"agent_b": f"B answered: {state['query']}!"}}


        @agents_manager.execution_wrappers.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)
        async def gather(state: GraphState) -> GraphState:
            # No-op: both agents wrote to distinct keys under results
            return state

        # Nodes
        graph.add_node("router", router)
        graph.add_node("agent_a", agent_a)
        graph.add_node("agent_b", agent_b)
        graph.add_node("gather", gather)

        # Edges: START -> router -(fan_out)-> [agent_a, agent_b] -> gather -> END
        graph.add_edge(START, "router")
        graph.add_conditional_edges("router", fan_out)  # branch to parallel agents once
        graph.add_edge("agent_a", "gather")
        graph.add_edge("agent_b", "gather")
        graph.add_edge("gather", END)

        app = graph.compile()

        wall_start = time.perf_counter()
        result = await app.ainvoke({"query": "What is parallelism?", "results": {}})
        wall_ms = (time.perf_counter() - wall_start) * 1000

        logging.info(f"WALL elapsed_ms={wall_ms:.1f} (expect ~2s, not ~4s)")
        print({"results": result.get("results")})


if __name__ == "__main__":
    
    asyncio.run(main())



"""
