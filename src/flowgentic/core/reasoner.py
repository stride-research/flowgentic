from flowgentic.core.models.model_provider import ModelProvider
from flowgentic.core.schemas.prompt_input import PromptInput
from flowgentic.core.schemas.providers_response import ProvidersResponse
from flowgentic.core.tool.tool import Tool

import logging

logger = logging.getLogger(__name__)


class Reasoner:
	def __init__(self, model_provider: ModelProvider):
		self.provider = model_provider
		self.tool_schemas = {}

	def bind_tool(self, tool: Tool):
		self.tool_schemas[tool.name] = tool.get_schema()

	async def plan(
		self, prompt_input: PromptInput, memory_context
	) -> ProvidersResponse:
		# 1) Plan what to do based on prompt + context
		tools = list(self.tool_schemas.values()) if self.tool_schemas else None
		provider_resp: ProvidersResponse = await self.provider.aprompt(
			prompt_input=prompt_input, memory_context=memory_context, tools=tools
		)
		# 2) Extract content of response
		logger.debug(f"Reasoner response is: {provider_resp}")
		return provider_resp
