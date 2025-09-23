from typing import Dict
from langgraph.graph import END, StateGraph
from abc import ABC, abstractmethod

from flowgentic.langGraph.main import LangraphIntegration


class BaseWorkflowBuilder(ABC):
	def __init__(self, agents_manager: LangraphIntegration):
		self.agents_manager = agents_manager

	@abstractmethod
	def build_workflow(self) -> StateGraph:
		pass


class BaseToolRegistry(ABC):
	def __init__(self, agents_manager: LangraphIntegration) -> None:
		self.agents_manager = agents_manager

		self.agent_tools = {}
		self.deterministic_tasks = {}

	def _register_all_tools(self):
		"""Register all tools and tasks through the unified interface."""
		self._register_agent_tools()
		self._register_utility_tasks()

	def get_task_by_name(self, tool_name: str):
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

	def get_toolset(self):
		return {
			"agent_tools": self.agent_tools,
			"deterministic_tasks": self.deterministic_tasks,
		}

	@abstractmethod
	def _register_agent_tools(self): ...
	@abstractmethod
	def _register_utility_tasks(self): ...


class BaseWorkflowNodes(ABC):
	def __init__(self, agents_manager: LangraphIntegration, tools_registry) -> None:
		self.agents_manager = agents_manager
		self.tools_registry = tools_registry

	@abstractmethod
	def get_all_nodes(self) -> Dict[str, callable]:
		pass


class BaseAgentTools(ABC):
	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tools = {}

	@abstractmethod
	def register_tools(self):
		pass


class BaseUtilsTasks(ABC):
	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tasks = {}

	@abstractmethod
	def register_tasks(self):
		pass
