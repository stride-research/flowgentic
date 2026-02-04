from functools import wraps
import time
import uuid
from typing import Any, Callable, Optional
from flowgentic.agent_orchestration_frameworks.base import AgentOrchestrator
from flowgentic.backend_engines.base import BaseEngine
from langchain_core.tools import tool as langchain_tool


class LanGraphOrchestrator(AgentOrchestrator):
	def __init__(self, engine: BaseEngine) -> None:
		self.engine = engine

	def hpc_tool(self, func: Optional[Callable] = None, **task_kwargs: Any):
		def deco(f: Callable):
			tool_name = getattr(f, "__name__", str(f))
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

			@wraps(f)
			async def wrapper(*args, **kwargs):
				return await self.engine.execute_tool(
					f, *args, task_kwargs=task_kwargs, **kwargs
				)

			wrapped_tool = langchain_tool(wrapper)

			# Emit setup end event
			self.engine.emit(
				{
					"event": "tool_wrap_end",
					"ts": time.perf_counter(),
					"tool_name": tool_name,
					"wrap_id": wrap_id,
				}
			)

			return wrapped_tool

		return deco(func) if callable(func) else deco

	def hpc_node(self, node_func: Callable):
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
