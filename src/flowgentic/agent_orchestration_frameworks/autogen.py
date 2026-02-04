from functools import wraps
import time
import uuid
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
		tool_name = getattr(func, "__name__", str(func))
		wrap_id = str(uuid.uuid4())

		# Emit setup start event
		self.engine.emit(
			{
				"event": "tool_wrap_start",
				"ts": time.perf_counter(),
				"tool_name": tool_name,
				"wrap_id": wrap_id,
			}
		)

		@wraps(func)
		async def wrapper(*args, **kwargs):
			return await self.engine.execute_tool(func, *args, **kwargs)

		# Emit setup end event
		self.engine.emit(
			{
				"event": "tool_wrap_end",
				"ts": time.perf_counter(),
				"tool_name": tool_name,
				"wrap_id": wrap_id,
			}
		)

		return wrapper

	def hpc_node(self, node_func: Callable):
		"""
		Wraps a function to be used as a custom Reply function in AutoGen.
		"""
		node_name = getattr(node_func, "__name__", str(node_func))
		wrap_id = str(uuid.uuid4())

		# Emit node wrap start event
		self.engine.emit(
			{
				"event": "node_wrap_start",
				"ts": time.perf_counter(),
				"node_name": node_name,
				"wrap_id": wrap_id,
			}
		)

		wrapped_node = self.engine.wrap_node(node_func)

		# Emit node wrap end event
		self.engine.emit(
			{
				"event": "node_wrap_end",
				"ts": time.perf_counter(),
				"node_name": node_name,
				"wrap_id": wrap_id,
			}
		)

		return wrapped_node
