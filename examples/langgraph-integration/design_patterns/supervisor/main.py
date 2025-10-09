"""
Supervisor Pattern Example with Radical AsyncFlow Execution

This example demonstrates the supervisor design pattern where a supervisor agent
dynamically routes work to specialized worker agents (researcher and calculator)
using Command/Send, while all execution happens through radical asyncflow.
"""

## TO BE DONE
# 1) Get interactive prompting
# 2) Adapt to radical asyncflow

from langchain_core.tools import tool
from pydantic import BaseModel
from radical.asyncflow import ConcurrentExecutionBackend
from concurrent.futures import ThreadPoolExecutor
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.utils.llm_providers import ChatLLMProvider

from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.prebuilt import InjectedState, create_react_agent
from langgraph.types import Command, Send
from langgraph.checkpoint.memory import InMemorySaver
from langchain_core.messages import (
	BaseMessage,
	HumanMessage,
	SystemMessage,
	AIMessage,
	ToolMessage,
)
from langchain_core.language_models.chat_models import BaseChatModel, SimpleChatModel
from langchain_core.runnables import RunnableConfig
from typing import Annotated, Any, Dict, List, Optional, Iterator
import os

import asyncio

from dotenv import load_dotenv

load_dotenv()


class WorkflowState(BaseModel):
	messages: Annotated[List[BaseMessage], add_messages] = []


def create_supervisor_workflow(agents_manager: LangraphIntegration):
	"""Build a supervisor workflow with dynamic routing via Command/Send.

	Uses the official LangGraph pattern: supervisor ReAct agent with handoff tools.
	"""

	# ==================== Worker Agent Tools ====================
	# These tools are executed via radical asyncflow

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
	)
	async def web_search(query: str) -> str:
		"""Search the web for information."""
		await asyncio.sleep(0.5)  # Simulate API call
		return f"Search results for '{query}': Found comprehensive information on the topic..."

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
	)
	async def calculate(expression: str) -> str:
		"""Evaluate a mathematical expression."""
		await asyncio.sleep(0.3)  # Simulate computation
		try:
			# Safe eval for demo - in production use a proper math parser
			result = eval(expression, {"__builtins__": {}}, {})
			return f"Result: {result}"
		except Exception as e:
			return f"Error evaluating expression: {str(e)}"

	# Create handoff tools for each worker agent
	transfer_to_researcher = agents_manager.execution_wrappers.create_task_description_handoff_tool(
		agent_name="researcher",
		description="Assign task to a researcher agent. Use this for information gathering, research, and general knowledge questions.",
	)

	transfer_to_calculator = agents_manager.execution_wrappers.create_task_description_handoff_tool(
		agent_name="calculator",
		description="Assign task to a calculator agent. Use this for mathematical calculations and numerical operations.",
	)

	# ==================== Supervisor Agent ====================
	# The supervisor is a ReAct agent with handoff tools (no domain tools)

	supervisor_agent = create_react_agent(
		model=ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash"),
		tools=[transfer_to_researcher, transfer_to_calculator],
		prompt=(
			"You are a supervisor managing two specialized agents:\n"
			"- Researcher Agent: Handles research, information gathering, and general knowledge questions\n"
			"- Calculator Agent: Handles mathematical calculations and numerical operations\n\n"
			"Your job is to:\n"
			"1. Analyze the user's request\n"
			"2. Decide which agent is best suited to handle it\n"
			"3. Use the appropriate transfer tool to assign the task with a clear description\n"
			"4. Transfer to ONE agent at a time (do not call multiple agents in parallel)\n"
			"5. Do not attempt to answer questions yourself - always delegate to a specialist\n\n"
			"Provide clear, detailed task descriptions when transferring work."
		),
		name="supervisor",
	)

	# ==================== Worker Agents ====================
	# Worker agents have domain-specific tools
	# They are wrapped in asyncflow execution blocks for HPC compatibility

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.EXECUTION_BLOCK
	)
	async def researcher_node(state: WorkflowState) -> WorkflowState:
		"""Researcher agent with web search capabilities."""
		print("🔍 Researcher Agent: Starting research...")

		researcher_agent = create_react_agent(
			model=ChatLLMProvider(
				provider="OpenRouter", model="google/gemini-2.5-flash"
			),
			tools=[web_search],
			name="researcher",
		)

		result = await researcher_agent.ainvoke(state)

		print("✅ Researcher Agent: Task complete")

		# Return updated state
		return {"messages": result["messages"]}

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.EXECUTION_BLOCK
	)
	async def calculator_node(state: WorkflowState) -> WorkflowState:
		"""Calculator agent with mathematical computation capabilities."""
		print("🧮 Calculator Agent: Starting calculation...")

		calculator_agent = create_react_agent(
			model=ChatLLMProvider(
				provider="OpenRouter", model="google/gemini-2.5-flash"
			),
			tools=[calculate],
			name="calculator",
		)

		result = await calculator_agent.ainvoke(state)

		print("✅ Calculator Agent: Task complete")

		# Return updated state
		return {"messages": result["messages"]}

	# ==================== Build Graph ====================
	# Following the official LangGraph supervisor pattern

	# Register nodes for introspection
	agents_manager.agent_introspector._all_nodes = [
		"supervisor",
		"researcher",
		"calculator",
	]

	# Wrap worker nodes with introspection
	# NOTE: DO NOT wrap the supervisor when using destinations parameter
	# The destinations parameter requires direct access to Command objects
	researcher_node_wrapped = agents_manager.agent_introspector.introspect_node(
		researcher_node, node_name="researcher"
	)
	calculator_node_wrapped = agents_manager.agent_introspector.introspect_node(
		calculator_node, node_name="calculator"
	)

	workflow = StateGraph(WorkflowState)

	workflow.add_node(
		"supervisor",
		supervisor_agent,  # Use unwrapped supervisor
		destinations=("researcher", "calculator"),
	)

	workflow.add_node("researcher", researcher_node_wrapped)
	workflow.add_node("calculator", calculator_node_wrapped)

	# Set entry point to supervisor
	workflow.add_edge(START, "supervisor")

	# Workers return to supervisor after completing their task
	# This enables multi-turn workflows where supervisor can delegate to multiple agents
	workflow.add_edge("researcher", "supervisor")
	workflow.add_edge("calculator", "supervisor")

	return workflow


async def start_app():
	"""Initialize and run the supervisor workflow."""
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		print("🚀 Starting Supervisor Pattern Example")
		print("=" * 80)

		# Build workflow
		workflow = create_supervisor_workflow(agents_manager)

		# Compile with memory
		memory = InMemorySaver()
		app = workflow.compile(checkpointer=memory)

		# Test case 1: Research query
		initial_state = {
			"messages": [
				HumanMessage(
					content="SEARCH IN THE WEB WHAT IS ANIMAL WITH LARGEST LIFESPAN IN THE SOUTHERN EAST REGION OF THE US"
				)
			]
		}

		print("\n📝 User Query:")
		print(
			"SEARCH IN THE WEB WHAT IS ANIMAL WITH LARGEST LIFESPAN IN THE SOUTHERN EAST REGION OF THE US"
		)
		print("=" * 80)

		try:
			# Execute workflow with recursion limit
			config = {
				"configurable": {"thread_id": "supervisor_demo"},
				"recursion_limit": 30,
			}

			# Stream execution
			async for chunk in app.astream(
				initial_state, config=config, stream_mode="values"
			):
				print(f"Chunk: {chunk}\n")

			print("\n" + "=" * 80)
			print("✅ Workflow completed successfully!")

		except Exception as e:
			print(f"\n❌ Workflow failed: {str(e)}")
			raise
		finally:
			pass
			# Generate telemetry
			agents_manager.agent_introspector.generate_report()
			await agents_manager.utils.render_graph(app)


if __name__ == "__main__":
	asyncio.run(start_app())
