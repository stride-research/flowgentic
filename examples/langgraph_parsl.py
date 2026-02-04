import asyncio
from typing import Annotated
from langgraph.graph import StateGraph, add_messages
from pydantic import BaseModel
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine
from concurrent.futures import ThreadPoolExecutor

from flowgentic.agent_orchestration_frameworks.langgraph import LanGraphOrchestrator
from flowgentic.backend_engines.parsl import ParslEngine
from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine

from flowgentic.core.models.implementations.dummy.langgraph import (
	DummyLanggraphModelProvider,
)
from flowgentic.old.utils.llm_providers import ChatLLMProvider
import logging
import time

from parsl.config import Config
from parsl.executors import ThreadPoolExecutor

from langgraph.prebuilt import ToolNode


logger = logging.getLogger(__name__)

from dotenv import load_dotenv

load_dotenv()


class WorkflowState(BaseModel):
	messages: Annotated[list, add_messages]


async def start_app():
	# --- SETUP HPC BACKEND ---
	parsl_config = Config(
		executors=[ThreadPoolExecutor(max_threads=1, label="local_threads")]
	)

	t_execution_start = time.perf_counter()

	# --- INITIALIZE FLOWGENTIC ---
	engine = ParslEngine(parsl_config)
	orchestrator = LanGraphOrchestrator(engine)

	# --- DEFINE HPC TOOLS ---
	@orchestrator.hpc_task
	async def fetch_temperature(location: str = "SFO"):
		"""Fetches temperature of a given city."""
		logger.debug(f"Executing temperature tool")
		await asyncio.sleep(2)
		return {"temperature": 70, "location": location}

	@orchestrator.hpc_task
	async def fetch_humidity(location: str = "SFO"):
		"""Fetches humidity of a given city."""
		logger.debug(f"Execute humidity tool")
		await asyncio.sleep(2)  # Simulate HPC latency/work
		return {"humidity": 50, "location": location}

	tools = [fetch_temperature, fetch_humidity]
	llm = DummyLanggraphModelProvider(calls_per_tool=1).bind_tools(tools)

	# --- DEFINE GRAPH NODES ---
	@orchestrator.hpc_block
	async def chatbot_logic(state: WorkflowState):
		response = await llm.ainvoke(state.messages)
		return {"messages": [response]}

	# --- CONDITIONAL EDGE UTILITIES ---
	def should_continue(state: WorkflowState):
		last_message = state.messages[-1]
		if hasattr(last_message, "tool_calls") and last_message.tool_calls:
			return "tools"
		return "end"

	# --- COMPILE GRAPH ---
	workflow = StateGraph(WorkflowState)
	workflow.add_node("agent", chatbot_logic)
	workflow.add_node("tools", ToolNode(tools))

	workflow.set_entry_point("agent")
	workflow.add_conditional_edges(
		"agent", should_continue, {"tools": "tools", "end": "__end__"}
	)
	workflow.add_edge("tools", "agent")  # Loop back to agent after tools
	workflow.set_entry_point("agent")
	workflow.set_finish_point("agent")

	app = workflow.compile()

	# --- EXECUTE ---
	input_state = {"messages": [("user", "Fetch temperature and humidity in SFO")]}
	result = await app.ainvoke(input_state)

	t_execution_end = time.perf_counter()

	logger.debug(f"RESULT IS: {result}, with: {t_execution_end - t_execution_start}")


if __name__ == "__main__":
	asyncio.run(start_app())
