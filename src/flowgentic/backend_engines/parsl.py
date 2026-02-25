import asyncio
import time
from typing import Any, Callable, Dict, Optional

import parsl
from parsl.app.app import python_app
from parsl.config import Config

from flowgentic.backend_engines.base import BaseEngine


class ParslEngine(BaseEngine):
	def __init__(
		self,
		config: Config = None,
		observer: Optional[Callable[[Dict[str, Any]], None]] = None,
	):
		super().__init__(observer=observer)
		parsl.clear()
		parsl.load(config)

		self._task_registry: Dict[str, Any] = {}

	def _make_parsl_app(self, func: Callable):
		if asyncio.iscoroutinefunction(func):

			def wrapper(*args, **kwargs):
				return asyncio.run(func(*args, **kwargs))

			return python_app(wrapper)
		return python_app(func)

	async def execute_tool(
		self,
		func: Callable,
		*args,
		task_kwargs: Optional[Dict[str, Any]] = None,
		invocation_id: Optional[str] = None,
		**kwargs,
	) -> Dict[str, Any]:
		task_name = getattr(func, "__name__", str(func))

		# Track whether the task app was already cached
		cache_hit = task_name in self._task_registry
		if not cache_hit:
			self._task_registry[task_name] = self._make_parsl_app(func)

		task_app = self._task_registry[task_name]

		# Ts_resolve_end: Task descriptor resolved from registry
		self.emit(
			{
				"event": "tool_resolve_end",
				"ts": time.perf_counter(),
				"tool_name": task_name,
				"invocation_id": invocation_id,
				"cache_hit": cache_hit,
			}
		)

		# Ts_bookkeep_end: Metadata done, about to enter Parsl
		self.emit(
			{
				"event": "tool_bookkeep_end",
				"ts": time.perf_counter(),
				"tool_name": task_name,
				"invocation_id": invocation_id,
			}
		)

		future = task_app(*args, **kwargs)
		result = await asyncio.to_thread(future.result)

		# Ts_collect_start: Result received from Parsl
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
		node_app = self._make_parsl_app(node_func)

		async def async_node_wrapper(*args, **kwargs):
			future = node_app(*args, **kwargs)
			return await asyncio.to_thread(future.result)

		return async_node_wrapper
	
	