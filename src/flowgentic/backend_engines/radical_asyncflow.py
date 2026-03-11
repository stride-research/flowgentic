import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

from radical.asyncflow import WorkflowEngine

from flowgentic.backend_engines.base import BaseEngine

logger = logging.getLogger(__name__)


class AsyncFlowEngine(BaseEngine):
	def __init__(
		self,
		flow: WorkflowEngine,
		observer: Optional[Callable[[Dict[str, Any]], None]] = None,
	):
		super().__init__(observer=observer)
		self.flow = flow
		self._task_registry: Dict[
			Tuple[Callable, Tuple[Tuple[str, Any], ...]], Any
		] = {}

	async def execute_tool(
		self,
		func: Callable,
		*args,
		task_kwargs: Optional[Dict[str, Any]] = None,
		invocation_id: Optional[str] = None,
		**kwargs,
	) -> Dict[str, Any]:
		task_kwargs = task_kwargs or {}
		key = (func, tuple(sorted(task_kwargs.items())))

		task_name = getattr(func, "__name__", str(func))

		# Track whether the task descriptor was already cached
		cache_hit = key in self._task_registry

		# Ts_resolve_end: Task descriptor resolved, about to enter AsyncFlow
		self.emit(
			{
				"event": "tool_resolve_end",
				"ts": time.perf_counter(),
				"tool_name": task_name,
				"invocation_id": invocation_id,
				"cache_hit": cache_hit,
			}
		)

		if not cache_hit:
			self._task_registry[key] = self.flow.function_task(func, **task_kwargs)

		task = self._task_registry[key]

		result = await task(*args, **kwargs)

		# Ts_collect_start: Result received from AsyncFlow
		self.emit(
			{
				"event": "tool_collect_start",
				"ts": time.perf_counter(),
				"tool_name": task_name,
				"invocation_id": invocation_id,
			}
		)

		return result

	def wrap_node(self, node_func: Callable):
		@self.flow.block
		async def node_block(*args, **kwargs):
			return await node_func(*args, **kwargs)

		async def async_node_wrapper(*args, **kwargs):
			return await node_block(*args, **kwargs)

		return async_node_wrapper
