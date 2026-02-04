from functools import wraps
from typing import Any, Callable

from flowgentic.agent_orchestration_frameworks.base import AgentOrchestrator
from flowgentic.backend_engines.base import BaseEngine


class AutoGenOrchestrator(AgentOrchestrator):
	def __init__(self, engine: BaseEngine) -> None:
		self.engine = engine

	def hpc_tool(self, func: Callable):
		"""
		Wraps a tool function so it executes via the flowgentic engine.
		autogen reads the signature from the wrapper (via @wraps).
		"""

		@wraps(func)
		async def wrapper(*args, **kwargs):
			return await self.engine.execute_tool(func, *args, **kwargs)

		return wrapper

	def hpc_node(self, node_func: Callable):
		"""
		Wraps a function to be used as a custom Reply function in AutoGen.
		"""
		return self.engine.wrap_node(node_func)
