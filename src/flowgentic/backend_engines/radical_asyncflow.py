from typing import Callable, Dict, Any, Optional, Tuple
import time
import uuid

import logging
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
		**kwargs,
	) -> Dict[str, Any]:
		task_kwargs = task_kwargs or {}
		key = (func, tuple(sorted(task_kwargs.items())))

		if key not in self._task_registry:
			# Pass wrapper-level params into task creation here
			self._task_registry[key] = self.flow.function_task(func, **task_kwargs)

		task = self._task_registry[key]
		task_name = getattr(func, "__name__", str(func))
		exec_id = str(uuid.uuid4())

		# Emit start event
		self.emit(
			{
				"event": "task_exec_start",
				"ts": time.perf_counter(),
				"task_name": task_name,
				"exec_id": exec_id,
			}
		)

		result = await task(*args, **kwargs)

		# Emit end event
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
		@self.flow.block
		async def node_block(*args, **kwargs):
			return await node_func(*args, **kwargs)

		async def async_node_wrapper(*args, **kwargs):
			return await node_block(*args, **kwargs)

		return async_node_wrapper
