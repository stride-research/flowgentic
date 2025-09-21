import logging
import uuid
import os
import asyncio
import json

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.graph.state import CompiledStateGraph
from flowgentic.langGraph.agents import BaseLLMAgentState


logger = logging.getLogger(__name__)


class LangraphUtils:
	def __init__(self) -> None:
		pass

	@staticmethod
	async def render_graph(app: CompiledStateGraph, file_name: str = None):
		"""Render graph visualization."""
		if file_name is None:
			file_name = f"workflow_graph_{uuid.uuid4()}.png"
		dir_name = "agent_run_data"
		if not os.path.exists(dir_name):
			os.makedirs(dir_name)
		file_path = f"{dir_name}/{file_name}"

		logger.info(f"Rendering graph to file: {file_name}")
		try:
			await asyncio.to_thread(app.get_graph().draw_png, file_path)
			logger.info(f"Graph successfully rendered to {file_name}")
		except Exception as e:
			logger.error(
				f"Failed to render graph to {file_name}: {type(e).__name__}: {str(e)}"
			)
			raise

	@staticmethod
	async def needs_tool_invokation(state: BaseLLMAgentState) -> str:
		"""Check if tool invocation is needed."""
		print(f"STATE IS {state}, with type {type(state)}")
		messages = state.messages
		if not messages:
			return "false"

		last_message = messages[-1]
		has_tool_calls = hasattr(last_message, "tool_calls") and last_message.tool_calls

		logger.debug(
			f"Last message type: {type(last_message).__name__}, has tool calls: {has_tool_calls}"
		)
		return "true" if has_tool_calls else "false"

	@staticmethod
	def structured_final_response(
		llm: BaseChatModel, response_schema: type, graph_state_schema: type
	):
		"""Create structured response formatter."""
		logger.info(
			f"Creating structured response formatter with schema: {response_schema.__name__}"
		)
		formatter_llm = llm.with_structured_output(response_schema)

		async def response_structurer(current_graph_state):
			logger.debug(
				f"Structuring response for {len(current_graph_state.messages)} messages"
			)
			try:
				messages = current_graph_state.messages
				result = await formatter_llm.ainvoke(messages)
				payload = result.model_dump()
				logger.debug(f"Successfully structured response: {payload}")
				return graph_state_schema(
					messages=[AIMessage(content=json.dumps(payload))]
				)
			except Exception as e:
				logger.error(
					f"Failed to structure response: {type(e).__name__}: {str(e)}"
				)
				raise

		return response_structurer
