"""
FEATURES:
      [x] Tools
      [] Memory
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
import json
import os
import pathlib
import random
from typing import Annotated
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, create_react_agent
from pydantic import BaseModel
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.langGraph.utils import LangraphUtils
from flowgentic.utils.llm_providers import ChatLLMProvider
from flowgentic.langGraph.main import LangraphIntegration

from dotenv import load_dotenv

load_dotenv()


class WorkflowState(BaseModel):
	messages: Annotated[list, add_messages]


class DayVerdict(BaseModel):
	looks_like_a_good_day: bool
	reason: str


"""
A wrapper that returns a task future output, but registers it first
"""


async def start_app():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		llm = ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash")

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def weather_extractor(city: str):
			"""Extracts the weather for any given city"""
			return {
				"temperature_celsius": 25,
				"humidity_percentage": 40,
			}  # Dummy example

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def traffic_extractor(city: str):
			"""Extracts the amount of traffic for any given city"""
			return {"traffic_percentage": 90}  # Dummy example

		# Define the task within the asyncflow context
		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def deterministic_task_internal(state: WorkflowState):
			file_path = "im-working.txt"
			with open(file_path, "w") as f:
				f.write("Hello world!")
			return {"status": "file_written", "path": file_path}

		tools = [weather_extractor, traffic_extractor]
		llm_with_tools = llm.bind_tools(tools)

		async def invoke_llm(state: WorkflowState):
			response = await llm_with_tools.ainvoke(state.messages)
			return WorkflowState(messages=[response])

		workflow = StateGraph(WorkflowState)

		# Nodes
		workflow.add_node("chatbot", invoke_llm)
		# Edges
		workflow.set_entry_point("chatbot")
		workflow.add_edge("chatbot", END)

		checkpointer = InMemorySaver()
		app = workflow.compile(checkpointer=checkpointer)
		thread_id = random.randint(0, 10)
		config = {"configurable": {"thread_id": thread_id}}

		current_dir = str(pathlib.Path(__file__).parent.resolve())

		await agents_manager.utils.render_graph(
			app, dir_to_write=current_dir, generate_graph_only=True
		)

		while True:
			user_input = input("User: ").lower()
			if user_input in ["quit", "q", "-q", "exit"]:
				print(f"Goodbye!")
				last_state = app.get_state(config)
				print(f"Last state: {last_state}")
				return

			current_state = WorkflowState(messages=[HumanMessage(content=user_input)])

			async for chunk in app.astream(
				current_state, stream_mode="values", config=config
			):
				if chunk["messages"]:
					last_msg = chunk["messages"][-1]
					if isinstance(last_msg, AIMessage):
						if hasattr(last_msg, "content") and last_msg.content:
							print(f"Assistant: {last_msg.content}")
						if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
							print(f"Tool calls: {last_msg.tool_calls}")
				print(chunk)
				print("=" * 30)


if __name__ == "__main__":
	asyncio.run(start_app())
