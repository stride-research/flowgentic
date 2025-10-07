from typing import List
from .tools import (
	ResearchTools,
	SynthesisTools,
	ValidationTasks,
	ContextTasks,
	FormattingTasks,
)

from flowgentic.langGraph.base_components import BaseToolRegistry


class ToolsRegistry(BaseToolRegistry):
	"""Unified interface for all workflow tools and tasks for the given usecase"""

	def __init__(self, agents_manager):
		super().__init__(agents_manager)

		# Initialize specialized tool/task managers
		self.research_tools = ResearchTools(agents_manager)
		self.synthesis_tools = SynthesisTools(agents_manager)
		self.validation_tasks = ValidationTasks(agents_manager)
		self.context_tasks = ContextTasks(agents_manager)
		self.formatting_tasks = FormattingTasks(agents_manager)

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

	def _register_function_tasks(self):
		"""Register all deterministic tasks from specialized managers."""
		validation_tasks = self.validation_tasks.register_function_tasks()
		context_tasks = self.context_tasks.register_function_tasks()
		formatting_tasks = self.formatting_tasks.register_function_tasks()

		self.deterministic_tasks.update(
			{
				**validation_tasks,
				**context_tasks,
				**formatting_tasks,
			}
		)
