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

	def hpc_task(self, func: Optional[Callable] = None, **task_kwargs: Any):
		"""Wrap a function to execute as an HPC task."""

		def deco(f: Callable):
			task_name = getattr(f, "__name__", str(f))
			wrap_id = str(uuid.uuid4())

			# Emit setup start event
			self.engine.emit(
				{
					"event": "task_wrap_start",
					"ts": time.perf_counter(),
					"task_name": task_name,
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
					"event": "task_wrap_end",
					"ts": time.perf_counter(),
					"task_name": task_name,
					"wrap_id": wrap_id,
				}
			)

			return wrapped_tool

		return deco(func) if callable(func) else deco

	def hpc_block(self, block_func: Callable):
		"""Wrap a function to execute as an HPC block."""
		block_name = getattr(block_func, "__name__", str(block_func))
		wrap_id = str(uuid.uuid4())

		# Emit block wrap start event
		self.engine.emit(
			{
				"event": "block_wrap_start",
				"ts": time.perf_counter(),
				"block_name": block_name,
				"wrap_id": wrap_id,
			}
		)

		wrapped_block = self.engine.wrap_node(block_func)

		# Emit block wrap end event
		self.engine.emit(
			{
				"event": "block_wrap_end",
				"ts": time.perf_counter(),
				"block_name": block_name,
				"wrap_id": wrap_id,
			}
		)

		return wrapped_block
