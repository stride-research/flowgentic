import logging
import uuid
import os
import asyncio
import json

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.graph.state import CompiledStateGraph
from flowgentic.langGraph.execution_wrappers import BaseLLMAgentState
from flowgentic.settings.extract_settings import APP_SETTINGS


logger = logging.getLogger(__name__)


class LangraphUtils:
	def __init__(self) -> None:
		pass

	@staticmethod
	async def render_graph(
		app: CompiledStateGraph, dir_to_write: str, generate_graph_only: bool = False
	):
		"""
		Renders a graph visualization and prepends it to a Markdown report.
		"""
		# Use os.path.join to ensure correct path construction
		report_output_path = (
			dir_to_write
			+ "/"
			+ APP_SETTINGS["agent_execution"]["execution_summary_path"]
		)
		image_output_path = (
			dir_to_write + "/" + APP_SETTINGS["agent_execution"]["graph_image_path"]
		)

		# Extract the relative image path for Markdown
		image_name = os.path.basename(image_output_path)

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

	def create_output_results_dirs(self, current_directory):
		results_directory = APP_SETTINGS["agent_execution"]["results_directory"]
		os.makedirs(current_directory + "/" + results_directory, exist_ok=True)
