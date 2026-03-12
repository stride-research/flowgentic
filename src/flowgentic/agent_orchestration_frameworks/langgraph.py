import time
import uuid
from functools import wraps
from typing import Any, Callable, Optional

from flowgentic.agent_orchestration_frameworks.base import AgentOrchestrator
from flowgentic.backend_engines.base import BaseEngine


def _langchain_tool(func: Callable) -> Callable:
	"""Lazy wrapper so langchain-core is only imported when actually used."""
	try:
		from langchain_core.tools import tool as _tool
	except ModuleNotFoundError as exc:
		raise ImportError(
			"langchain-core is required for LanGraphOrchestrator. "
			'Install it with: pip install "flowgentic[langgraph]"'
		) from exc
	return _tool(func)


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
					"event": "tool_wrap_start",
					"ts": time.perf_counter(),
					"tool_name": task_name,
					"wrap_id": wrap_id,
				}
			)

			@wraps(f)
			async def wrapper(*args, **kwargs):
				invocation_id = str(uuid.uuid4())

				# Ts_invoke_start: LangGraph calls tool, FlowGentic intercepts
				self.engine.emit(
					{
						"event": "tool_invoke_start",
						"ts": time.perf_counter(),
						"tool_name": task_name,
						"invocation_id": invocation_id,
					}
				)

				result = await self.engine.execute_tool(
					f,
					*args,
					task_kwargs=task_kwargs,
					invocation_id=invocation_id,
					**kwargs,
				)

				# Ts_collect_end: Result returned to LangGraph
				self.engine.emit(
					{
						"event": "tool_invoke_end",
						"ts": time.perf_counter(),
						"tool_name": task_name,
						"invocation_id": invocation_id,
					}
				)

				return result

			wrapped_tool = _langchain_tool(wrapper)

			# Emit setup end event
			self.engine.emit(
				{
					"event": "tool_wrap_end",
					"ts": time.perf_counter(),
					"tool_name": task_name,
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
