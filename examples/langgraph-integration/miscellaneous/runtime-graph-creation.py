import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from radical.asyncflow import ConcurrentExecutionBackend
from typing_extensions import TypedDict
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessageGraph
from langgraph.graph.state import CompiledStateGraph, StateGraph
from langgraph.graph.message import add_messages
from langchain.tools import tool
from langgraph.prebuilt import ToolNode
from langchain_core.runnables import RunnableConfig

from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.utils.llm_providers import ChatLLMProvider

load_dotenv()


class State(TypedDict):
	messages: Annotated[list[BaseMessage], add_messages]


model = ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash")


async def make_default_graph(agents_manager) -> CompiledStateGraph:
	"""Make a simple LLM agent"""
	graph_workflow = StateGraph(State)

	@agents_manager.execution_wrappers.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)
	async def call_model(state):
		return {"messages": [model.invoke(state["messages"])]}

	graph_workflow.add_node("agent", call_model)
	graph_workflow.add_edge("agent", END)
	graph_workflow.set_entry_point("agent")

	agent = graph_workflow.compile()
	return agent


async def make_alternative_graph(agents_manager) -> CompiledStateGraph:
	"""Make a tool-calling agent"""

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
	)
	async def add(a: float, b: float):
		"""Adds two numbers."""
		return a + b

	tool_node = ToolNode([add])
	model_with_tools = model.bind_tools([add])

	@agents_manager.execution_wrappers.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)
	async def call_model(state):
		return {"messages": [model_with_tools.invoke(state["messages"])]}

	def should_continue(state: State):
		if state["messages"][-1].tool_calls:
			return "tools"
		else:
			return END

	graph_workflow = StateGraph(State)

	graph_workflow.add_node("agent", call_model)
	graph_workflow.add_node("tools", tool_node)
	graph_workflow.add_edge("tools", "agent")
	graph_workflow.set_entry_point("agent")
	graph_workflow.add_conditional_edges("agent", should_continue)

	agent = graph_workflow.compile()
	return agent


async def make_graph(config: RunnableConfig):
	user_id = config.get("configurable", {}).get("user_id")

	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	# Keep the context manager alive during the entire execution
	async with LangraphIntegration(backend=backend) as agents_manager:
		# route to different graph state / structure based on the user ID
		if user_id == "1":
			graph = await make_default_graph(agents_manager)
		else:
			graph = await make_alternative_graph(agents_manager)

		curent_state = State(
			messages=[HumanMessage("What tools do you have in your toolkit?")]
		)

		async for chunk in graph.astream(curent_state, stream_mode="values", config={}):
			if chunk["messages"]:
				last_msg = chunk["messages"][-1]
				if isinstance(last_msg, AIMessage):
					if hasattr(last_msg, "content") and last_msg.content:
						print(f"Assistant: {last_msg.content}")
					if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
						print(f"Tool calls: {last_msg.tool_calls}")
				print(chunk)
				print("=" * 30)
	return graph


if __name__ == "__main__":
	asyncio.run(make_graph(config={"configurable": {"user_id": "1"}}))
	asyncio.run(make_graph(config={"configurable": {"user_id": "2"}}))
