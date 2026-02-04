from abc import ABC, abstractmethod
from typing import Any, Callable, List, Optional, Tuple, Dict


class BaseEngine(ABC):
	def __init__(self, observer: Optional[Callable[[Dict[str, Any]], None]] = None):
		"""
		Args:
			observer: Optional callback that receives events for profiling/benchmarking.
					  No-op by default. Signature: (event: Dict) -> None
		"""
		self._observer = observer

	def emit(self, event: Dict[str, Any]) -> None:
		"""Emit an event to the observer if one is registered."""
		if self._observer is not None:
			self._observer(event)

	@abstractmethod
	async def execute_tool(
		self,
		func: Callable,
		*args,
		task_kwargs: Optional[Dict[str, Any]] = None,
		**kwargs,
	) -> Dict[str, Any]:
		pass

	@abstractmethod
	async def wrap_node(self, node_func: Callable):
		pass
