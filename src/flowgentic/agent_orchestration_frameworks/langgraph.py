from functools import wraps
from typing import Callable
from flowgentic.agent_orchestration_frameworks.base import AgentOrchestrator
from flowgentic.backend_engines.base import BaseEngine
from langchain_core.tools import tool as langchain_tool


class LanGraphOrchestrator(AgentOrchestrator):
	def __init__(self, engine: BaseEngine) -> None:
		self.engine = engine

	def hpc_tool(self, func: Callable):
		@wraps(func)
		async def wrapper(*args, **kwargs):
			return await self.engine.execute_tool(func, *args, **kwargs)

		return langchain_tool(wrapper)

	def hpc_node(self, node_func: Callable):
		return self.engine.wrap_node(node_func)
