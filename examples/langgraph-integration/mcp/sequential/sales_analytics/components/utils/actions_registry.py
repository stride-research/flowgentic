"""
Unified registry for all sales analytics tools and tasks.
Follows same pattern as financial_advisor for consistency.
"""

from typing import List
from .actions import (
	DatabaseTools,
	AnalyticsTools,
	ReportTools,
	ValidationTasks,
	ContextTasks,
	FormattingTasks,
)

from flowgentic.langGraph.base_components import BaseToolRegistry


class ActionsRegistry(BaseToolRegistry):
	"""Unified interface for all sales analytics workflow tools and tasks."""

	def __init__(self, agents_manager):
		super().__init__(agents_manager)

		# Initialize specialized tool/task managers
		self.database_tools = DatabaseTools(agents_manager)
		self.analytics_tools = AnalyticsTools(agents_manager)
		self.report_tools = ReportTools(agents_manager)
		self.validation_tasks = ValidationTasks(agents_manager)
		self.context_tasks = ContextTasks(agents_manager)
		self.formatting_tasks = FormattingTasks(agents_manager)

	def _register_agent_tools(self):
		"""Register all agent tools from specialized managers."""
		database_tools = self.database_tools.register_tools()
		analytics_tools = self.analytics_tools.register_tools()
		report_tools = self.report_tools.register_tools()

		self.agent_tools.update(
			{
				**database_tools,
				**analytics_tools,
				**report_tools,
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
