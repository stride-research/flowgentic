from abc import ABC, abstractmethod
from typing import Any, Callable, List, Tuple, Dict


class BaseEngine(ABC):
	@abstractmethod
	async def execute_tool(self, func: Callable, *args, **kwargs) -> Dict[str, Any]:
		pass

	@abstractmethod
	async def wrap_node(self, node_func: Callable):
		pass
