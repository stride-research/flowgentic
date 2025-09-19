"""

FEATURES:
      [] Tools
      [] Memory

"""

import asyncio
import os
from typing import Annotated
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
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


async def invoke_llm(state: WorkflowState):
	response = await llm.ainvoke(state.messages)
	return WorkflowState(messages=[response])


workflow = StateGraph(WorkflowState)
workflow.add_node("chatbot", invoke_llm)
workflow.set_entry_point("chatbot")
workflow.add_edge("chatbot", END)

app = workflow.compile()


async def start_app():
	while True:
		user_input = input("User: ").lower()
		if user_input in ["quit", "q", "-q", "exit"]:
			print(f"Goodbye!")
			return

		initial_state = WorkflowState(messages=[HumanMessage(content=user_input)])

		async for chunk in app.astream(initial_state, stream_mode="values"):
			print(chunk)


asyncio.run(start_app())
