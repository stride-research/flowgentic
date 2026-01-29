import asyncio
import json
from typing import Callable, List, Tuple, Dict, Any
from radical.asyncflow import WorkflowEngine
from flowgentic.backend_engines.base import BaseEngine
from flowgentic.core.tool.tool import Tool

import logging

logger = logging.getLogger(__name__)


class AsyncFlowEngine(BaseEngine):
	def __init__(self, flow: WorkflowEngine):
		self.flow = flow
		self._task_registry = {}

	async def execute_tool(self, func: Callable, *args, **kwargs) -> Dict[str, Any]:
		if func.__name__ not in self._task_registry:
			self._task_registry[func.__name__] = self.flow.function_task(func)

		task = self._task_registry[func.__name__]
		return await task(*args, **kwargs)

	def wrap_node(self, node_func: Callable):
		@self.flow.block
		async def node_block(*args, **kwargs):
			return await node_func(*args, **kwargs)

		async def async_node_wrapper(*args, **kwargs):
			return await node_block(*args, **kwargs)

		return async_node_wrapper
