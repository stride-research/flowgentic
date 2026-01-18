from typing import Optional
from flowgentic.backend_engines.base import BaseEngine
from flowgentic.core.memory.agent_state import AgentState
from flowgentic.core.memory.memory_manager import MemoryManger, NullMemoryManager
from flowgentic.core.reasoner import Reasoner
from flowgentic.core.schemas import providers_response
from flowgentic.core.schemas.agent_response import AgentResponse
from flowgentic.core.schemas.prompt_input import PromptInput
from flowgentic.core.tool.tool import Tool

import logging

logger = logging.getLogger(__name__)


class Agent:
	def __init__(
		self,
		reasoner: Reasoner,
		engine: BaseEngine,
		memory_manager: Optional[MemoryManger] = None,
	):
		self.reasoner = reasoner
		self.engine = engine
		self.memory = (
			memory_manager if memory_manager is not None else NullMemoryManager()
		)
		self.tools_registry = {}

	def add_tool(self, tool: Tool):
		self.reasoner.bind_tool(tool)  # Track tools and their schema
		self.tools_registry[tool.name] = tool  # Track tools and their callables

	async def _internal_run_logic(self, prompt_input: PromptInput):
		# 1. Memory retreival
		state = AgentState()
		state.add_user_input(prompt_input)
		context = self.memory.get_context()

		# 3. Reasoning (the brain of the agent)
		providers_response = await self.reasoner.plan(prompt_input, context)
		reasoning = providers_response.reasoning
		message = providers_response.message
		tools_to_use = providers_response.tools_to_use
		state.add_reasoning(reasoning)
		state.add_message(message)

		# 4. Execution (the muscle of the agent)
		results = await self.engine.execute_tools(tools_to_use, self.tools_registry)
		state.add_tool_results(results)

		# 5. Update memory
		self.memory.record_state(state)

		agent_response = AgentResponse(
			tools_results=results, reasoning=reasoning, message=message
		)
		logger.debug(f"Agent response is: {agent_response}")
		return agent_response

	async def run(self, prompt_input: PromptInput):
		return await self.engine.wrap_agent_run(self._internal_run_logic, prompt_input)
