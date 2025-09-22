from typing import List
from .tools import (
	ResearchTools,
	SynthesisTools,
	ValidationTasks,
	ContextTasks,
	FormattingTasks,
)


class ToolsRegistry:
	"""Unified interface for all workflow tools and tasks."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager

		# Initialize specialized tool/task managers
		self.research_tools = ResearchTools(agents_manager)
		self.synthesis_tools = SynthesisTools(agents_manager)
		self.validation_tasks = ValidationTasks(agents_manager)
		self.context_tasks = ContextTasks(agents_manager)
		self.formatting_tasks = FormattingTasks(agents_manager)

		# Unified interfaces
		self.agent_tools = {}
		self.deterministic_tasks = {}

	def register_all_tools(self):
		"""Register all tools and tasks through the unified interface."""
		self._register_agent_tools()
		self._register_utility_tasks()

	def _register_agent_tools(self):
		"""Register all agent tools from specialized managers."""
		research_tools = self.research_tools.register_tools()
		synthesis_tools = self.synthesis_tools.register_tools()

		self.agent_tools.update(
			{
				**research_tools,
				**synthesis_tools,
			}
		)

	def _register_utility_tasks(self):
		"""Register all deterministic tasks from specialized managers."""
		validation_tasks = self.validation_tasks.register_tasks()
		context_tasks = self.context_tasks.register_tasks()
		formatting_tasks = self.formatting_tasks.register_tasks()

		self.deterministic_tasks.update(
			{
				**validation_tasks,
				**context_tasks,
				**formatting_tasks,
			}
		)

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
