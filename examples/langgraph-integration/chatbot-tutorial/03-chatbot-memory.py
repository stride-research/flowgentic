"""

FEATURES:
      [X] Tools
      [X] Memory

"""

import asyncio
from email import message
import os
from typing import Annotated
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel

from dotenv import load_dotenv

load_dotenv()


class WorkflowState(BaseModel):
	messages: Annotated[list, add_messages]


llm = ChatOpenAI(
	model="google/gemini-2.5-flash",
	openai_api_base="https://openrouter.ai/api/v1",
	openai_api_key=os.getenv("OPEN_ROUTER_API_KEY"),
)


@tool
async def weather_extractor(city: str):
	"""Extracts the weather for any given city"""
	return {"temperature_celsius": 25, "humidity_percentage": 40}  # Dummy example


tools = [weather_extractor]
llm_with_tools = llm.bind_tools(tools)


async def invoke_llm(state: WorkflowState):
	response = await llm_with_tools.ainvoke(state.messages)
	return WorkflowState(messages=[response])


async def needs_tool_invokation(state: WorkflowState) -> str:
	last_message = state.messages[-1]
	if (
		hasattr(last_message, "tool_calls") and last_message.tool_calls
	):  # Ensuring there is a tool call attr and is not empty
		return "true"
	return "false"


workflow = StateGraph(WorkflowState)
workflow.add_node("chatbot", invoke_llm)
workflow.set_entry_point("chatbot")
workflow.add_node("tools", ToolNode(tools))
workflow.add_conditional_edges(
	"chatbot", needs_tool_invokation, {"true": "tools", "false": END}
)
workflow.add_edge("tools", "chatbot")

app = workflow.compile()


async def start_app():
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
