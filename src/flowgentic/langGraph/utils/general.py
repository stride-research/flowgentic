import logging
import uuid
import os
import asyncio
import json

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.graph.state import CompiledStateGraph
from flowgentic.langGraph.execution_wrappers import BaseLLMAgentState
from flowgentic.utils.telemetry.report_generator import GRAPH_IMAGE, REPORT_FILE


logger = logging.getLogger(__name__)


class LangraphUtils:
	def __init__(self) -> None:
		pass

	@staticmethod
	async def render_graph(
		app: CompiledStateGraph, dir_to_write: str, generate_graph_only: bool = False
	):
		"""
		Renders a graph visualization into dir_to_write and prepends it to the
		Markdown report there.
		"""
		report_output_path = os.path.join(dir_to_write, REPORT_FILE)
		image_output_path = os.path.join(dir_to_write, GRAPH_IMAGE)
		image_name = GRAPH_IMAGE

		if not generate_graph_only:
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
