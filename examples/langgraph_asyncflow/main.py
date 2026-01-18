"""
1. Basic => agent says hello to prompt
2. Integration with asyncflow
3. Basic with one non-llm node at the end
4. Basic with two non-llm nodes with routing
      - What happens if we attempt non-LLM node?
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import random
from typing import Annotated

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, add_messages
from pydantic import BaseModel
from flowgentic.core.agent import Agent
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.core.models.implementations.openRouter import OpenRouterModelProvider

from dotenv import load_dotenv

from flowgentic.core.reasoner import Reasoner
from flowgentic.core.schemas.prompt_input import PromptInput

load_dotenv()


class WorkflowState(BaseModel):
	messages: Annotated[list, add_messages]


async def start_app():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	reasoner_model = OpenRouterModelProvider(
		model_id="google/gemini-3-flash-preview",
		api_key=os.getenv("OPEN_ROUTER_API_KEY"),
	)
	reasoner = Reasoner(model_provider=reasoner_model)

	agent = Agent(reasoner=reasoner)

	async def chatbot_node(state: WorkflowState):
		last_message = state.messages[-1].content

		prompt_input = PromptInput(
			user_input=last_message,
			system_input="You are a helpful assistant that reminds people's names.",
		)

		response = await agent.run(prompt_input)

		return {"messages": [AIMessage(content=str(response))]}

	# 1) Structure of the workflow
	workflow = StateGraph(WorkflowState)
	workflow.add_node("chatbot", chatbot_node)
	workflow.set_entry_point("chatbot")
	workflow.set_finish_point("chatbot")

	# 2) Settings of the workflow
	checkpointer = InMemorySaver()
	compiled_workflow = workflow.compile(checkpointer=checkpointer)
	thread_id = random.randint(0, 10)
	config = {"configurable": {"thread_id": thread_id}}

	compiled_workflow = workflow.compile(checkpointer)

	while True:
		user_input = input("User: ").lower()
		if user_input in ["quit", "q", "-q", "exit"]:
			print(f"Goodbye!")
			last_state = compiled_workflow.get_state(config)
			print(f"Last state: {last_state}")
			return

		current_state = WorkflowState(messages=[HumanMessage(content=user_input)])

		async for chunk in compiled_workflow.astream(
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
