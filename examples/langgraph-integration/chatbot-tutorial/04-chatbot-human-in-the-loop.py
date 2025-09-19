"""

      [x] Tools
      [x] Memory
      [x] Human in the loop

Usecases of human-in-the-loop:
      Approvals & Safeguards
      e.g. before sending an email, filing a ticket, or triggering a production change, the graph pauses so a human can confirm or reject.

      Disambiguation / Clarification
      if the LLM is uncertain (“Did you mean project Alpha or Beta?”), it can stop and ask the user for input rather than guessing.

      Escalation Paths
      customer support assistants may try automated responses first, but if confidence is low or the query is sensitive, the graph hands off to a human agent.

      Interactive Editing
      workflows where the AI drafts something (plan, SQL query, code) and then pauses for the human to revise or approve before execution continues.

      Debugging / Experimentation
      while developing a graph, you can insert a human checkpoint to inspect the state at that point in execution, tweak it, and then resume.
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
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

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


@tool
async def human_assistance(query: str):
	"""Request assistance from a human."""
	human_response = interrupt({"query": query})
	return human_response["data"]


tools = [weather_extractor, human_assistance]
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

# Memory mechanism
memory = InMemorySaver()
app = workflow.compile(checkpointer=memory)
conversation_id = "1"
config = {"configurable": {"thread_id": conversation_id}}


async def start_app():
	while True:
		user_input = input("User: ").lower()
		if user_input in ["quit", "q", "-q", "exit"]:
			print(f"Goodbye!")
			return

		initial_state = WorkflowState(messages=[HumanMessage(content=user_input)])

		async for chunk in app.astream(
			input=initial_state, config=config, stream_mode="values"
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


asyncio.run(start_app())
