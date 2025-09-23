"""
Minimal LangGraph example demonstrating parallel tool execution using AsyncFlow integration.

This example shows:
1. LLM makes a decision and calls multiple tools
2. Tools execute in parallel through AsyncFlow
3. Results are collected and returned to LLM
"""

import asyncio
import os
from typing import List, Annotated
from pydantic import BaseModel
import logging
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

from concurrent.futures import ThreadPoolExecutor
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.langGraph import LangGraphIntegration, RetryConfig

from radical.asyncflow.logging import init_default_logger

logger = logging.getLogger(__name__)

init_default_logger(logging.INFO)

load_dotenv()


# State
class State(BaseModel):
	model_config = {"arbitrary_types_allowed": True}

	messages: Annotated[List[BaseMessage], add_messages]


async def main():
	# Initialize AsyncFlow
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())
	flow = await WorkflowEngine.create(backend=backend)
	integration = LangGraphIntegration(flow)

	# Define AsyncFlow tools that will execute in parallel
	@integration.asyncflow_tool(
		retry=RetryConfig(
			max_attempts=3, base_backoff_sec=0.5, max_backoff_sec=4.0, timeout_sec=10.0
		)
	)
	async def fetch_weather(city: str) -> str:
		"""Get weather for a city."""
		logger.info(f"[AsyncFlow] Fetching weather for {city}...")
		await asyncio.sleep(2)  # Simulate API call
		return f"Weather in {city}: Sunny, 22°C"

	@integration.asyncflow_tool(
		# Simulate a slightly more patient policy for news
		retry=RetryConfig(
			max_attempts=4, base_backoff_sec=0.5, max_backoff_sec=6.0, timeout_sec=15.0
		)
	)
	async def fetch_news(topic: str) -> str:
		"""Get news about a topic."""
		logger.info(f"[AsyncFlow] Fetching news about {topic}...")
		await asyncio.sleep(1.5)  # Simulate API call
		return f"Latest news on {topic}: Market trends looking positive"

	@integration.asyncflow_tool(
		retry=RetryConfig(
			max_attempts=2, base_backoff_sec=0.4, max_backoff_sec=2.0, timeout_sec=8.0
		)
	)
	async def calculate_distance(city1: str, city2: str) -> str:
		"""Calculate distance between cities."""
		logger.info(f"[AsyncFlow] Calculating distance {city1} -> {city2}...")
		await asyncio.sleep(1)  # Simulate calculation
		return f"Distance from {city1} to {city2}: 245 km"

	tools = [fetch_weather, fetch_news, calculate_distance]

	# LangGraph nodes
	async def llm_node(state: State):
		"""LLM decides what to do and calls tools."""
		llm = ChatOpenAI(
			temperature=0.3,
			model="google/gemini-2.5-pro",
			openai_api_base="https://openrouter.ai/api/v1",
			openai_api_key=os.getenv("OPEN_ROUTER_API_KEY"),
			max_retries=3,
			timeout=30,
		)

		llm_with_tools = llm.bind_tools(tools)
		response = await llm_with_tools.ainvoke(state["messages"])
		return {"messages": [response]}

	def should_continue(state: State):
		"""Check if we should continue to tools or end."""
		last_message = state["messages"][-1]
		if hasattr(last_message, "tool_calls") and last_message.tool_calls:
			return "tools"
		return "end"

	# Build graph
	workflow = StateGraph(State)
	workflow.add_node("llm", llm_node)
	workflow.add_node("tools", ToolNode(tools))

	workflow.set_entry_point("llm")
	workflow.add_conditional_edges(
		"llm", should_continue, {"tools": "tools", "end": END}
	)
	workflow.add_edge("tools", "llm")

	# Enable checkpointing so the graph can resume gracefully
	app = workflow.compile()

	# Test the parallel execution
	logger.info("🚀 Testing parallel tool execution...")
	logger.info("=" * 50)

	# This prompt will trigger the LLM to call multiple tools in parallel
	user_message = """I'm planning a trip from Paris to London. Can you:
    1. Get the weather in London
    2. Find news about travel
    3. Calculate the distance between Paris and London

    Please do all of these at once."""

	logger.info(f"User: {user_message}")
	logger.info("\n🤖 LLM Response and Tool Execution:")

	start_time = asyncio.get_event_loop().time()

	# Run the workflow - tools will execute in parallel through AsyncFlow
	final_state = await app.ainvoke({"messages": [HumanMessage(content=user_message)]})

	end_time = asyncio.get_event_loop().time()

	# Show final response
	final_response = final_state["messages"][-1]
	logger.info(f"\n✅ Final Response: {final_response.content}")
	logger.info(f"⏱️ Total execution time: {end_time - start_time:.2f} seconds")
	logger.info("\n💡 Note: The tools executed in parallel through AsyncFlow!")
	logger.info("   If they ran sequentially, it would take ~4.5 seconds")
	logger.info("   With parallel execution, it should be closer to ~2 seconds")

	await flow.shutdown()


if __name__ == "__main__":
	asyncio.run(main())
