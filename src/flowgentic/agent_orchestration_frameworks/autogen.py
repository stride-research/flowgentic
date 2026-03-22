from functools import wraps
import time
import uuid
from typing import Any, Callable

from flowgentic.agent_orchestration_frameworks.base import AgentOrchestrator
from flowgentic.backend_engines.base import BaseEngine


class AutoGenOrchestrator(AgentOrchestrator):
	def __init__(self, engine: BaseEngine) -> None:
		self.engine = engine

	def hpc_task(self, func: Callable):
		"""
		Wraps a function to execute as an HPC task.
		AutoGen reads the signature from the wrapper (via @wraps).
		"""
		task_name = getattr(func, "__name__", str(func))
		wrap_id = str(uuid.uuid4())

		self.engine.emit(
			{
				"event": "tool_wrap_start",
				"ts": time.perf_counter(),
				"tool_name": task_name,
				"wrap_id": wrap_id,
			}
		)

		@wraps(func)
		async def wrapper(*args, **kwargs):
			invocation_id = str(uuid.uuid4())
			self.engine.emit(
				{
					"event": "tool_invoke_start",
					"ts": time.perf_counter(),
					"tool_name": task_name,
					"invocation_id": invocation_id,
				}
			)
			result = await self.engine.execute_tool(
				func, *args, invocation_id=invocation_id, **kwargs
			)
			self.engine.emit(
				{
					"event": "tool_invoke_end",
					"ts": time.perf_counter(),
					"tool_name": task_name,
					"invocation_id": invocation_id,
				}
			)
			return result

		self.engine.emit(
			{
				"event": "tool_wrap_end",
				"ts": time.perf_counter(),
				"tool_name": task_name,
				"wrap_id": wrap_id,
			}
		)

		return wrapper

	def hpc_block(self, block_func: Callable):
		"""
		Wraps a function to execute as an HPC block (coordinated work unit).
		"""
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
