from typing import Callable, Dict, Any, Optional, Tuple

import logging
from radical.asyncflow import WorkflowEngine
from flowgentic.backend_engines.base import BaseEngine

logger = logging.getLogger(__name__)


class AsyncFlowEngine(BaseEngine):
	def __init__(self, flow: WorkflowEngine):
		self.flow = flow
		self._task_registry: Dict[
			Tuple[Callable, Tuple[Tuple[str, Any], ...]], Any
		] = {}

	async def execute_tool(
		self,
		func: Callable,
		*args,
		task_kwargs: Optional[Dict[str, Any]] = None,
		**kwargs,
	) -> Dict[str, Any]:
		task_kwargs = task_kwargs or {}
		key = (func, tuple(sorted(task_kwargs.items())))

		if key not in self._task_registry:
			# Pass wrapper-level params into task creation here
			self._task_registry[key] = self.flow.function_task(func, **task_kwargs)

		task = self._task_registry[key]
		return await task(*args, **kwargs)

	def wrap_node(self, node_func: Callable):
		@self.flow.block
		async def node_block(*args, **kwargs):
			return await node_func(*args, **kwargs)

		async def async_node_wrapper(*args, **kwargs):
			return await node_block(*args, **kwargs)

		return async_node_wrapper
