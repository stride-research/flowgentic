from typing import Dict
from langgraph.graph import END, StateGraph
from abc import ABC, abstractmethod

from flowgentic.langGraph.main import LangraphIntegration


class BaseToolRegistry(ABC):
	def __init__(self, agents_manager: LangraphIntegration) -> None:
		self.agents_manager = agents_manager
		self.agent_tools = {}
		self.deterministic_tasks = {}

	def _register_toolkit(self):
		"""Register all tools and tasks through the unified interface."""
		self._register_agent_tools()
		self._register_function_tasks()

	def get_function_task_by_name(self, tool_name: str):
		"""Get a specific tool by name."""
		if tool_name in self.deterministic_tasks:
			return self.deterministic_tasks[tool_name]
		else:
			raise ValueError(f"Task '{tool_name}' not found")

	def get_tool_by_name(self, tool_name: str):
		"""Get a specific tool by name."""
		if tool_name in self.agent_tools:
			return self.agent_tools[tool_name]
		else:
			raise ValueError(f"Tool '{tool_name}' not found")

	@abstractmethod
	def _register_agent_tools(self): ...
	@abstractmethod
	def _register_function_tasks(self): ...
