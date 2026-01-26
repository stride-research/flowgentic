import asyncio
import parsl
from parsl.app.app import python_app
from typing import Callable, Dict, Any
from parsl.config import Config
from flowgentic.backend_engines.base import BaseEngine


class ParslEngine(BaseEngine):
	def __init__(self, config: Config = None):
		# 1. Load config (whether Local or Slurm)
		if config:
			try:
				parsl.load(config)
			except RuntimeError:
				pass
		self._task_registry = {}

	def _make_parsl_app(self, func: Callable):
		# 2. Prepare function for Worker execution (Remote side)
		# If tool is async, wrap it so the sync Parsl worker can run it.
		if asyncio.iscoroutinefunction(func):

			def wrapper(*args, **kwargs):
				import asyncio

				return asyncio.run(func(*args, **kwargs))

			return python_app(wrapper)
		return python_app(func)

	async def execute_tool(self, func: Callable, *args, **kwargs) -> Dict[str, Any]:
		if func.__name__ not in self._task_registry:
			self._task_registry[func.__name__] = self._make_parsl_app(func)

		task_app = self._task_registry[func.__name__]

		# 3. Submit to Slurm/Local Executor
		future = task_app(*args, **kwargs)

		# 4. Wait for result on the Login Node without blocking the Agent
		return await asyncio.to_thread(future.result)

	def wrap_node(self, node_func: Callable):
		node_app = self._make_parsl_app(node_func)

		async def async_node_wrapper(*args, **kwargs):
			future = node_app(*args, **kwargs)
			return await asyncio.to_thread(future.result)

		return async_node_wrapper
