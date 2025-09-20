"""

FEATURES:
      [x] Tools
      [] Memory

"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from typing import Annotated
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, create_react_agent
from pydantic import BaseModel
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic import (
	ChatLLMProvider,
	LangGraphIntegration,
	RetryConfig,
	BaseLLMAgentState,
)


from dotenv import load_dotenv

load_dotenv()


class WorkflowState(BaseLLMAgentState): ...


class DayVerdict(BaseModel):
	looks_like_a_good_day: bool
	reason: str


async def start_app():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangGraphIntegration(backend=backend) as orchestrator:
		llm = ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash")

		@orchestrator.asyncflow_tool()
		async def weather_extractor(city: str):
			"""Extracts the weather for any given city"""
			return {
				"temperature_celsius": 25,
				"humidity_percentage": 40,
			}  # Dummy example

		@orchestrator.asyncflow_tool()
		async def traffic_extractor(city: str):
			"""Extracts the amount of traffic for any given city"""
			return {"traffic_percentage": 90}  # Dummy example

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
			orchestrator.structured_final_response(
				llm=llm, response_schema=DayVerdict, graph_state_schema=WorkflowState
			),
		)
		workflow.set_entry_point("chatbot")
		workflow.add_node("tools", ToolNode(tools))
		# Edges
		workflow.add_conditional_edges(
			"chatbot",
			orchestrator.needs_tool_invokation,
			{"true": "tools", "false": "response_synthetizer"},
		)
		workflow.add_edge("tools", "chatbot")
		workflow.add_edge("response_synthetizer", END)

		app = workflow.compile()
		await orchestrator.render_graph(app)

		while True:
			user_input = input("User: ").lower()
			if user_input in ["quit", "q", "-q", "exit"]:
				print(f"Goodbye!")
				return

			initial_state = WorkflowState(messages=[HumanMessage(content=user_input)])

			async for chunk in app.astream(initial_state, stream_mode="values"):
				if chunk["messages"]:
					last_msg = chunk["messages"][-1]
					if isinstance(last_msg, AIMessage):
						if hasattr(last_msg, "content") and last_msg.content:
							print(f"Assistant: {last_msg.content}")
						if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
							print(f"Tool calls: {last_msg.tool_calls}")
				print(chunk)
				print("=" * 30)


asyncio.run(start_app())
