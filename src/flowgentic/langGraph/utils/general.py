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
	async def render_graph(app: CompiledStateGraph, dir_to_write: str):
		"""
		Renders a graph visualization into dir_to_write and prepends it to the
		Markdown report there, creating the report if there is none.
		"""
		report_output_path = os.path.join(dir_to_write, REPORT_FILE)
		os.makedirs(dir_to_write, exist_ok=True)

		# Drawing needs pygraphviz, which Flowgentic does not install: without
		# it, or if drawing fails, the report stays as it is
		try:
			await asyncio.to_thread(
				app.get_graph().draw_png, os.path.join(dir_to_write, GRAPH_IMAGE)
			)
		except Exception as e:
			logger.warning("Could not render the graph into %s: %s", dir_to_write, e)
			return

		# Read the existing content, if there is a report yet
		existing_content = ""
		if os.path.exists(report_output_path):
			with open(report_output_path, "r") as f:
				existing_content = f.read()
		new_content = f"# MULTI-AGENT GRAPH\n\n![Graph]({GRAPH_IMAGE})\n\n"

		# Write the combined content back to the report file
		with open(report_output_path, "w") as f:
			f.write(new_content + existing_content)
