from typing import Optional
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
		self, reasoner: Reasoner, memory_manager: Optional[MemoryManger] = None
	):
		self.reasoner = reasoner
		self.memory = (
			memory_manager if memory_manager is not None else NullMemoryManager()
		)
		self.tools = {}

	def add_tool(self, tool: Tool):
		self.reasoner.bind_tool(tool)
		self.tools[tool.name] = tool

	async def run(self, prompt_input: PromptInput):
		# 1. Intialize a fresh state for this specific run
		state = AgentState()
		state.add_user_input(prompt_input)

		# 2. Get context from memory
		context = self.memory.get_context()

		# 3. Reasoner plans the transformation into a Tool call
		providers_response = await self.reasoner.plan(prompt_input, context)
		reasoning = providers_response.reasoning
		message = providers_response.message
		tools_to_use = providers_response.tools_to_use
		state.add_reasoning(reasoning)
		state.add_message(message)

		# 4. Execute the Tool
		results = {}  # {tool_name: execution_result}
		for tool_name, args in tools_to_use:
			logger.debug(f"Executing tool with name: {tool_name}")
			results[tool_name] = self.tools[tool_name].execute(**args)
		state.add_tool_results(results)

		# 5. Flush state
		self.memory.record_state(state)
		agent_response = AgentResponse(
			tools_results=results, reasoning=reasoning, message=message
		)

		logger.debug(f"Agent response is: {agent_response}")

		return agent_response
