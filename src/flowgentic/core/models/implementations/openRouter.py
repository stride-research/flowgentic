from openai.types.chat import ChatCompletion
from flowgentic.core.models.model_provider import ModelProvider
from flowgentic.core.schemas.prompt_input import PromptInput

import openai

from flowgentic.core.schemas.providers_response import ProvidersResponse
import logging

logger = logging.getLogger(__name__)


class OpenRouterModelProvider(ModelProvider):
	def __init__(self, model_id: str, **config):
		super().__init__(model_id, **config)
		# Initialize client once
		self.aclient = openai.AsyncOpenAI(
			base_url="https://openrouter.ai/api/v1", api_key=config.get("api_key")
		)

	async def aprompt(
		self,
		prompt_input: PromptInput,
		memory_context: str,
		tools: list = None,
		**kwargs,
	) -> ProvidersResponse:
		messages = self._prepare_messages(prompt_input, memory_context)
		if tools:
			kwargs["tools"] = tools
		response: ChatCompletion = await self.aclient.chat.completions.create(
			model=self.model_id, messages=messages, **kwargs
		)
		logger.debug(f"Chat completition from OpenRotuer is: {response}")
		msg = response.choices[0].message.content
		reasoning = getattr(response.choices[0].message, "reasoning", None)
		tools_to_use = response.choices[0].message.tool_calls

		return ProvidersResponse(
			message=msg,
			reasoning=reasoning,
			tools_to_use=tools_to_use if tools_to_use else [],
		)
