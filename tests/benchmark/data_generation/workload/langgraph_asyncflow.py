import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
from typing import Annotated, List, Tuple

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, add_messages
from pydantic import BaseModel
from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine
from flowgentic.core.agent import Agent
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.core.models.implementations.dummyProvider import DummyModelProvider

from flowgentic.core.reasoner import Reasoner
from flowgentic.core.schemas.prompt_input import PromptInput
from flowgentic.core.tool.tool import Tool


class WorkflowState(BaseModel):
	messages: Annotated[list, add_messages]


async def langgraph_asyncflow_workload(
	reasoner_model: DummyModelProvider,
	n_of_backend_slots: int,
	n_of_agents: int,
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

	@flow.function_task
	async def flowgentic_setup(agent_id: int):
		"""Setup for a single agent - this will be parallelized by backend slots"""
		reasoner = Reasoner(model_provider=reasoner_model)
		engine = AsyncFlowEngine(flow)
		agent = Agent(reasoner=reasoner, engine=engine)

		async def chatbot_node(state: WorkflowState):
			last_message = state.messages[-1].content
			prompt_input = PromptInput(
				user_input=last_message,
				system_input="You call different tools.",
			)
			response = await agent.run(prompt_input)
			return {"messages": [AIMessage(content=str(response))]}

		async def fetch_temperature(location: str = "SFO") -> dict:
			"""Fetches temperature of a given city"""
			await asyncio.sleep(tool_execution_duration_time)
			return {"temperature": 70}

		agent.add_tool(Tool(fetch_temperature))
		return chatbot_node

	# === Set-up ===
	t_flowgentic_start = time.perf_counter()
	setup_futures = [flowgentic_setup(i) for i in range(n_of_agents)]
	chatbot_nodes = await asyncio.gather(*setup_futures)
	t_flowgentic_end = time.perf_counter()

	# === PARALLEL execution of all agents ===
	async def run_single_agent(chatbot_node):
		workflow = StateGraph(WorkflowState)
		workflow.add_node("chatbot", chatbot_node)
		workflow.set_entry_point("chatbot")
		workflow.set_finish_point("chatbot")
		compiled_workflow = workflow.compile()

		user_input = "Whats the weather in SFO?"
		current_state = WorkflowState(messages=[HumanMessage(content=user_input)])
		return await compiled_workflow.ainvoke(current_state)

	# Run ALL agents in parallel - this is the critical fix!
	results = await asyncio.gather(*[run_single_agent(node) for node in chatbot_nodes])

	t_execution_end = time.perf_counter()

	return (
		t_flowgentic_end - t_flowgentic_start,  # Wall-clock for setup phase
		t_execution_end - t_execution_start,  # Total execution time
		list(results),
	)
