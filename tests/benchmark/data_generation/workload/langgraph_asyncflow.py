import asyncio
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Annotated

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
	tool_execution_duration_time: int,
):
	"""
	Simulates worklaod for langgraph, asyncflow combination.
	Return future for execution of workload
	"""

	reasoner = Reasoner(model_provider=reasoner_model)
	# Backend engine
	backend = await ConcurrentExecutionBackend(
		ProcessPoolExecutor(max_workers=n_of_backend_slots)
	)
	flow = await WorkflowEngine.create(backend)
	engine = AsyncFlowEngine(flow)

	agent = Agent(reasoner=reasoner, engine=engine)

	# Primary agent
	async def chatbot_node(state: WorkflowState):
		last_message = state.messages[-1].content

		prompt_input = PromptInput(
			user_input=last_message,
			system_input="You call different tools.",
		)

		response = await agent.run(prompt_input)

		return {"messages": [AIMessage(content=str(response))]}

	# Tools, Primary agent
	async def fetch_temperature(location: str = "SFO") -> dict:
		"""Fetches temperature of a given city"""
		await asyncio.sleep(tool_execution_duration_time)
		return {"temperature": 70}

	agent.add_tool(Tool(fetch_temperature))

	# 1) Structure of the workflow
	workflow = StateGraph(WorkflowState)
	workflow.add_node("chatbot", chatbot_node)
	workflow.set_entry_point("chatbot")
	workflow.set_finish_point("chatbot")

	# 2) Settings of the workflow
	compiled_workflow = workflow.compile()

	user_input = "Whats the weather in SFO?"
	current_state = WorkflowState(messages=[HumanMessage(content=user_input)])
	return await compiled_workflow.ainvoke(current_state)
