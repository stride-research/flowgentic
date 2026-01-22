"""
1. Basic => agent says hello to prompt
2. Integration with asyncflow
      2.3 Allow for kwargs
3. Add tools
4. Basic with one non-llm node at the end
5. Basic with two non-llm nodes with routing
      - What happens if we attempt non-LLM node?
"""

import asyncio
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import time
from typing import Annotated

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, add_messages
from pydantic import BaseModel
from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine
from flowgentic.core.agent import Agent
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.core.models.implementations.dummyProvider import DummyModelProvider
from flowgentic.core.models.implementations.openRouter import OpenRouterModelProvider

from dotenv import load_dotenv

from flowgentic.core.reasoner import Reasoner
from flowgentic.core.schemas.prompt_input import PromptInput
from flowgentic.core.tool.tool import Tool

load_dotenv()


class WorkflowState(BaseModel):
	messages: Annotated[list, add_messages]


async def start_app():
	# Reasoner
	# reasoner_model = OpenRouterModelProvider(
	# 	model_id="google/gemini-3-flash-preview",
	# 	api_key=os.getenv("OPEN_ROUTER_API_KEY"),
	# )
	reasoner_model = DummyModelProvider(
		model_id="dummy/dummy_moodel",
		tool_names=["fetch_temperature"],
		n_of_tool_calls=3,
	)
	backend = await ConcurrentExecutionBackend(ProcessPoolExecutor(max_workers=3))
	flow = await WorkflowEngine.create(backend)
	start_time = time.perf_counter()

	reasoner = Reasoner(model_provider=reasoner_model)

	# Backend engine
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
		await asyncio.sleep(3)
		return {"temperature": 70}

	# async def fetch_humidity(location: str = "SFO") -> dict:
	# 	"""Fetches humidity of a given city"""
	# 	return {"humidity": 50}

	agent.add_tool(Tool(fetch_temperature))
	# agent.add_tool(Tool(fetch_humidity))

	# 1) Structure of the workflow
	workflow = StateGraph(WorkflowState)
	workflow.add_node("chatbot", chatbot_node)
	workflow.set_entry_point("chatbot")
	workflow.set_finish_point("chatbot")

	# 2) Settings of the workflow
	compiled_workflow = workflow.compile()

	user_input = "Whats the weather in SFO?"
	current_state = WorkflowState(messages=[HumanMessage(content=user_input)])
	await compiled_workflow.ainvoke(current_state)
	end_time = time.perf_counter()
	print(f"MAKESPAN: {end_time - start_time}")


if __name__ == "__main__":
	asyncio.run(start_app())
