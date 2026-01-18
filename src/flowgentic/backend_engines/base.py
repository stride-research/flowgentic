from abc import ABC, abstractmethod
from typing import Any, List, Tuple, Dict


class BaseEngine(ABC):
	@abstractmethod
	async def execute_tools(
		self, tools_to_use: List[Tuple[str, Dict]], tools: Dict[str, Any]
	) -> Dict[str, Any]:
		pass

	@abstractmethod
	async def wrap_agent_run(self, agent_logic_fn, *args, **kwargs):
		pass
