"""
FEATURES:
      [x] Tools
      [] Memory
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
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

from flowgentic.langGraph.utils import LangraphUtils
from flowgentic.utils.llm_providers import ChatLLMProvider
from flowgentic.langGraph.agent_logger import AgentLogger
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.agents import BaseLLMAgentState, LangraphAgents

from dotenv import load_dotenv

load_dotenv()


class WorkflowState(BaseLLMAgentState):
	pass


class DayVerdict(BaseModel):
	looks_like_a_good_day: bool
	reason: str


async def start_app():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		llm = ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash")

		@agents_manager.agents.asyncflow_tool()
		async def weather_extractor(city: str):
			"""Extracts the weather for any given city"""
			return {
				"temperature_celsius": 25,
				"humidity_percentage": 40,
			}  # Dummy example

		@agents_manager.agents.asyncflow_tool()
		async def traffic_extractor(city: str):
			"""Extracts the amount of traffic for any given city"""
			return {"traffic_percentage": 90}  # Dummy example

		# Define the task within the asyncflow context
		@agents_manager.agents.flow.function_task
		async def deterministic_task_internal():
			file_path = "im-working.txt"
			with open(file_path, "w") as f:
				f.write("Hello world!")
			return {"status": "file_written", "path": file_path}

		# Create a wrapper function that properly bridges to asyncflow
		async def deterministic_task_node(state: WorkflowState):
			"""Node wrapper that executes the asyncflow task and returns state"""
			try:
				# Execute the asyncflow task
				task_future = deterministic_task_internal()
				result = await task_future

				# Return the state (LangGraph expects state to be returned)
				return state
			except Exception as e:
				print(f"Error in deterministic_task: {e}")
				# Return state even on error to prevent workflow failure
				return state

		tools = [weather_extractor, traffic_extractor]
		llm_with_tools = llm.bind_tools(tools)

		async def invoke_llm(state: WorkflowState):
			response = await llm_with_tools.ainvoke(state.messages)
			return WorkflowState(messages=[response])

		workflow = StateGraph(WorkflowState)

		# Nodes
		workflow.add_node("chatbot", invoke_llm)
		workflow.add_node(
			"response_synthetizer",
			agents_manager.utils.structured_final_response(
				llm=llm, response_schema=DayVerdict, graph_state_schema=WorkflowState
			),
		)
		workflow.set_entry_point("chatbot")
		workflow.add_node("tools", ToolNode(tools))

		# Use the async wrapper instead of the decorated function directly
		workflow.add_node("deterministic_task", deterministic_task_node)

		# Edges
		workflow.add_conditional_edges(
			"chatbot",
			agents_manager.utils.needs_tool_invokation,
			{"true": "tools", "false": "response_synthetizer"},
		)
		workflow.add_edge("tools", "chatbot")
		workflow.add_edge("response_synthetizer", "deterministic_task")
		workflow.add_edge("deterministic_task", END)

		checkpointer = InMemorySaver()
		app = workflow.compile(checkpointer=checkpointer)
		thread_id = random.randint(0, 10)
		config = {"configurable": {"thread_id": thread_id}}

		await agents_manager.utils.render_graph(app)

		while True:
			user_input = input("User: ").lower()
			if user_input in ["quit", "q", "-q", "exit"]:
				print(f"Goodbye!")
				last_state = app.get_state(config)
				print(f"Last state: {last_state}")
				agents_manager.agent_logger.flush_agent_conversation(
					conversation_history=last_state.values.get("messages", [])
				)
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
