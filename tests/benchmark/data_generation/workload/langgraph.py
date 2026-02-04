import asyncio
from typing import Annotated
from langgraph.graph import StateGraph, add_messages
from pydantic import BaseModel
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine
from concurrent.futures import ThreadPoolExecutor

from flowgentic.agent_orchestration_frameworks.langgraph import LanGraphOrchestrator
from flowgentic.backend_engines.base import BaseEngine
from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine

from flowgentic.core.models.implementations.dummy.langgraph import (
	DummyLanggraphModelProvider,
)
import logging
import time

from langgraph.prebuilt import ToolNode


from tests.benchmark.data_generation.utils.schemas import (
	WorkloadConfig,
	WorkloadResult,
)
from tests.benchmark.data_generation.workload.base_workload import BaseWorkload


logger = logging.getLogger(__name__)

from dotenv import load_dotenv

load_dotenv()


class WorkflowState(BaseModel):
	messages: Annotated[list, add_messages]


class LangraphWorkload(BaseWorkload):
	"""
	Defines langgraph workload with configurable:
		- number of agents
		-tool calls per agents
	"""

	def __init__(self, workload_config: WorkloadConfig) -> None:
		super().__init__(workload_config=workload_config)

	async def run(self, engine: BaseEngine) -> WorkloadResult:
		t_execution_start = time.perf_counter()

		# --- INITIALIZE FLOWGENTIC ---
		orchestrator = LanGraphOrchestrator(engine)

		# --- DEFINE HPC TOOLS ---
		@orchestrator.hpc_tool(service=True)
		async def fetch_temperature(location: str = "SFO"):
			"""Fetches temperature of a given city."""
			logger.debug(f"Executing temperature tool")
			await asyncio.sleep(self.tool_execution_duration_time)
			return {"temperature": 70, "location": location}

		@orchestrator.hpc_tool
		async def fetch_humidity(location: str = "SFO"):
			"""Fetches humidity of a given city."""
			logger.debug(f"Execute humidity tool")
			await asyncio.sleep(self.tool_execution_duration_time)
			return {"humidity": 50, "location": location}

		tools = [fetch_temperature, fetch_humidity]
		llm = DummyLanggraphModelProvider(
			calls_per_tool=self.n_of_tool_calls_per_agent
		).bind_tools(tools)

		# --- DEFINE GRAPH NODES ---
		@orchestrator.hpc_node
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
		async def instantiate_agent():
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
			input_state = {
				"messages": [("user", "Fetch temperature and humidity in SFO")]
			}
			return await app.ainvoke(input_state)

		workloads = [instantiate_agent() for i in range(self.n_of_agents)]
		results = await asyncio.gather(*workloads)

		t_execution_end = time.perf_counter()

		return WorkloadResult(
			total_makespan=t_execution_end - t_execution_start,
			total_overhead_makespan=-1.0,
		)
