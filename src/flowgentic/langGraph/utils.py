import logging
import uuid
import os
import asyncio
import json

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.graph.state import CompiledStateGraph
from flowgentic.langGraph.agents import BaseLLMAgentState
from flowgentic.settings.extract_settings import APP_SETTINGS


logger = logging.getLogger(__name__)


class LangraphUtils:
	def __init__(self) -> None:
		pass

	@staticmethod
	async def render_graph(app: CompiledStateGraph):
		"""
		Renders a graph visualization and prepends it to a Markdown report.
		"""
		# Use os.path.join to ensure correct path construction
		report_output_path = APP_SETTINGS["agent_execution"]["execution_summary_path"]
		image_output_path = APP_SETTINGS["agent_execution"]["graph_image_path"]

		# Extract the relative image path for Markdown
		image_name = os.path.basename(image_output_path)

		try:
			os.makedirs(os.path.dirname(report_output_path), exist_ok=True)

			await asyncio.to_thread(app.get_graph().draw_png, image_output_path)

			# Read the existing content
			if os.path.exists(report_output_path):
				with open(report_output_path, "r") as f:
					existing_content = f.read()
			new_content = f"# MULTI-AGENT GRAPH\n\n![Graph]({image_name})\n\n"

			# Write the combined content back to the report file
			with open(report_output_path, "w") as f:
				f.write(new_content + existing_content)

		except Exception as e:
			print(f"Error rendering graph or writing to file: {e}")

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
