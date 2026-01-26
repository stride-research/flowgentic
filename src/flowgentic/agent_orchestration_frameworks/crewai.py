from functools import wraps
from typing import Callable
import inspect

from flowgentic.agent_orchestration_frameworks.base import AgentOrchestrator
from flowgentic.backend_engines.base import BaseEngine


class CrewAIOrchestrator(AgentOrchestrator):
	def __init__(self, engine: BaseEngine) -> None:
		self.engine = engine

	def hpc_tool(self, func: Callable):
		"""
		Wraps a tool function so it executes via the flowgentic engine.
		Creates a CrewAI BaseTool-compatible tool.
		"""
		from crewai.tools import BaseTool

		# Get function metadata
		func_name = func.__name__
		func_doc = func.__doc__ or f"Tool: {func_name}"

		# Get function signature for type hints
		sig = inspect.signature(func)
		func_params = {name: param.annotation for name, param in sig.parameters.items()}

		# Create a wrapper that handles async execution
		@wraps(func)
		async def async_wrapper(*args, **kwargs):
			return await self.engine.execute_tool(func, *args, **kwargs)

		# Create a sync wrapper that runs the async function
		def sync_wrapper(*args, **kwargs):
			import asyncio

			try:
				loop = asyncio.get_event_loop()
			except RuntimeError:
				loop = asyncio.new_event_loop()
				asyncio.set_event_loop(loop)
			return loop.run_until_complete(async_wrapper(*args, **kwargs))

		# Create a CrewAI BaseTool subclass
		class HPCTool(BaseTool):
			name: str = func_name
			description: str = func_doc.strip()

			def _run(self, *args, **kwargs) -> str:
				"""Execute the tool via the HPC engine."""
				result = sync_wrapper(*args, **kwargs)
				# Convert result to string if needed (CrewAI tools return strings)
				if isinstance(result, (dict, list)):
					import json

					return json.dumps(result)
				return str(result)

		# Set the tool class name
		HPCTool.__name__ = f"{func_name}_tool"

		return HPCTool()

	def hpc_node(self, node_func: Callable):
		"""
		Wraps a function to be used as a custom node in CrewAI workflows.
		"""
		return self.engine.wrap_node(node_func)
