from typing import List
from .actions import (
	MarketResearchTools,
	RiskAnalysisTools,
	PortfolioStrategyTools,
	ValidationTasks,
	ContextTasks,
	FormattingTasks,
)

from flowgentic.langGraph.base_components import BaseToolRegistry


class ActionsRegistry(BaseToolRegistry):
	"""Unified interface for all financial workflow tools and tasks."""

	def __init__(self, agents_manager):
		super().__init__(agents_manager)

		# Initialize specialized tool/task managers
		self.market_research_tools = MarketResearchTools(agents_manager)
		self.risk_analysis_tools = RiskAnalysisTools(agents_manager)
		self.portfolio_strategy_tools = PortfolioStrategyTools(agents_manager)
		self.validation_tasks = ValidationTasks(agents_manager)
		self.context_tasks = ContextTasks(agents_manager)
		self.formatting_tasks = FormattingTasks(agents_manager)

	def _register_agent_tools(self):
		"""Register all agent tools from specialized managers."""
		market_tools = self.market_research_tools.register_tools()
		risk_tools = self.risk_analysis_tools.register_tools()
		strategy_tools = self.portfolio_strategy_tools.register_tools()

		self.agent_tools.update(
			{
				**market_tools,
				**risk_tools,
				**strategy_tools,
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
