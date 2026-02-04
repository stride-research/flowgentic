import asyncio
import time
import uuid
import parsl
from parsl.app.app import python_app
from typing import Callable, Dict, Any, Optional
from parsl.config import Config
from flowgentic.backend_engines.base import BaseEngine


class ParslEngine(BaseEngine):
	def __init__(self, config: Config = None, observer: Optional[Callable[[Dict[str, Any]], None]] = None):
		super().__init__(observer=observer)
		parsl.clear()
		parsl.load(config)

		self._task_registry = {}

	def _make_parsl_app(self, func: Callable):
		if asyncio.iscoroutinefunction(func):

			def wrapper(*args, **kwargs):
				return asyncio.run(func(*args, **kwargs))

			return python_app(wrapper)
		return python_app(func)

	async def execute_tool(self, func: Callable, *args, task_kwargs: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
		if func.__name__ not in self._task_registry:
			self._task_registry[func.__name__] = self._make_parsl_app(func)

		task_app = self._task_registry[func.__name__]
		task_name = getattr(func, "__name__", str(func))
		exec_id = str(uuid.uuid4())

		self.emit(
			{
				"event": "task_exec_start",
				"ts": time.perf_counter(),
				"task_name": task_name,
				"exec_id": exec_id,
			}
		)

		future = task_app(*args, **kwargs)
		result = await asyncio.to_thread(future.result)

		self.emit(
			{
				"event": "task_exec_end",
				"ts": time.perf_counter(),
				"task_name": task_name,
				"exec_id": exec_id,
			}
		)

		return result

	def wrap_node(self, node_func: Callable):
		node_app = self._make_parsl_app(node_func)

		async def async_node_wrapper(*args, **kwargs):
			future = node_app(*args, **kwargs)
			return await asyncio.to_thread(future.result)

		return async_node_wrapper
