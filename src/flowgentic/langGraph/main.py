from typing import Optional, Any, Dict
import logging

from radical.asyncflow.workflow_manager import BaseExecutionBackend, WorkflowEngine
from flowgentic.langGraph.agents import LangraphAgents
from flowgentic.langGraph.utils import LangraphUtils
from flowgentic.langGraph.agent_logger import AgentLogger


logger = logging.getLogger(__name__)


class LangraphIntegration:
	"""Enhanced integration between AsyncFlow WorkflowEngine and LangChain tools.

	Supports both traditional workflow patterns and React agent orchestration
	with supervisor patterns and parallel execution.
	"""

	def __init__(self, backend: BaseExecutionBackend):
		logger.info(
			f"Initializing LangGraphIntegration with backend: {type(backend).__name__}"
		)
		self.backend = backend

	async def __aenter__(self):
		logger.info("Creating WorkflowEngine for LangGraphIntegration")
		self.flow = await WorkflowEngine.create(backend=self.backend)
		self.agents: LangraphAgents = LangraphAgents(flow=self.flow)
		self.utils: LangraphUtils = LangraphUtils()
		self.agent_logger: AgentLogger = AgentLogger()

		logger.info("WorkflowEngine created successfully")
		return self

	async def __aexit__(self, exc_type, exc, tb):
		logger.info("Shutting down WorkflowEngine")
		if exc_type:
			logger.warning(
				f"Exception occurred during context manager: {exc_type.__name__}: {exc}"
			)
		if self.flow:
			await self.flow.shutdown()
		logger.info("WorkflowEngine shutdown complete")
