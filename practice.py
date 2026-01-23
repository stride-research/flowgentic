"""
Core challenge:
	0. Use old interface
	1. Run multipe agents as per defined in the parameters of the function 'langgraph_asyncflow_workload'
	2. Each agent is a graph that has one tool which is fetch_temperature
	3. That tool needs to be invoekd as long as per defined in the parameters of the function 'langgraph_asyncflow_workload'
	4. Get dummy llm that doesnt connect to OpenAI or OpenRouter and always demands to invoke the tools that were passed to it
Notes:
	- Dont worry about the overhead measuremnt. Thats part of task 2 which I will do
	- How to execute => create an indepedent file where you see if ur implementation actually executs bunch of agents in parallel and executes bunch of tools
	-
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
from typing import List, Tuple

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.old.langGraph.execution_wrappers import (
	AsyncFlowType,
	BaseLLMAgentState,
	ExecutionWrappersLangraph,
)
from flowgentic.old.utils.dummy_llm import DummyToolCallingLLM
from flowgentic.old.utils.telemetry.introspection import GraphIntrospector


async def langgraph_asyncflow_workload(
	n_of_backend_slots: int,
	n_of_agents: int,
	n_of_tool_calls: int,
	tool_execution_duration_time: int,
) -> Tuple[float, float, List]:
	"""
	Simulates workload with SHARED backend across all agents.
	This allows backend slots to parallelize both setup work and tool execution.
	Returns (flowgentic_overhead_wallclock, execution_time, results)
	"""

	t_execution_start = time.perf_counter()

	# SHARED backend engine across all agents
	backend = await ConcurrentExecutionBackend(
		ThreadPoolExecutor(max_workers=n_of_backend_slots)
	)
	flow = await WorkflowEngine.create(backend)
	execution_wrappers = ExecutionWrappersLangraph(
		flow=flow, instrospector=GraphIntrospector()
	)
	dummy_llm = DummyToolCallingLLM(
		tool_names=["fetch_temperature"],
		n_of_tool_calls=n_of_tool_calls,
	)

	@execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION,
		tool_description="Fetches temperature of a given city",
	)
	async def fetch_temperature(location: str = "SFO") -> dict:
		await asyncio.sleep(tool_execution_duration_time)
		return {"temperature": 70}

	@flow.function_task
	async def flowgentic_setup(agent_id: int):
		"""Setup for a single agent - this will be parallelized by backend slots"""

		async def chatbot_node(state: BaseLLMAgentState):
			last_message = state.messages[-1].content
			response = await dummy_llm.ainvoke([HumanMessage(content=last_message)])
			return {"messages": [response]}

		return chatbot_node

	# === Set-up ===
	t_flowgentic_start = time.perf_counter()
	setup_futures = [flowgentic_setup(i) for i in range(n_of_agents)]
	chatbot_nodes = await asyncio.gather(*setup_futures)
	t_flowgentic_end = time.perf_counter()

	# === PARALLEL execution of all agents ===
	async def run_single_agent(chatbot_node):
		workflow = StateGraph(BaseLLMAgentState)
		workflow.add_node("chatbot", chatbot_node)
		workflow.add_node("tools", ToolNode([fetch_temperature]))
		workflow.add_edge(START, "chatbot")
		workflow.add_edge("chatbot", "tools")
		workflow.add_edge("tools", END)
		compiled_workflow = workflow.compile()

		user_input = "Whats the weather in SFO?"
		current_state = BaseLLMAgentState(messages=[HumanMessage(content=user_input)])
		return await compiled_workflow.ainvoke(current_state)

	# Run ALL agents in parallel
	results = await asyncio.gather(*[run_single_agent(node) for node in chatbot_nodes])

	t_execution_end = time.perf_counter()

	return (
		t_flowgentic_end - t_flowgentic_start,  # Wall-clock for setup phase
		t_execution_end - t_execution_start,  # Total execution time
		list(results),
	)
