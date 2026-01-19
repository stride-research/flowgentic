from typing import List
from openai.types.chat import ChatCompletion, ChatCompletionMessageFunctionToolCall
from openai.types.chat.chat_completion_message_tool_call import Function
from flowgentic.core.models.model_provider import ModelProvider
from flowgentic.core.schemas.prompt_input import PromptInput

from flowgentic.core.schemas.providers_response import ProvidersResponse
import logging

logger = logging.getLogger(__name__)


class DummyModelProvider(ModelProvider):
	"""This dummy model provider request to call all the functions that were passed to it"""

	def __init__(
		self, model_id: str, tool_names: List[str], n_of_tool_calls: int, **config
	):
		super().__init__(model_id, **config)
		self.tool_names = tool_names
		self.n_of_tool_calls = n_of_tool_calls

	async def aprompt(
		self,
		prompt_input: PromptInput,
		memory_context: str,
		tools: list = None,
		**kwargs,
	) -> ProvidersResponse:
		name = self.tool_names[0]  # Always caling the first function
		tools_to_use = [
			ChatCompletionMessageFunctionToolCall(
				id=f"tool_{name}_{i}",
				function=Function(name=name, arguments="{}"),
				type="function",
				index=i,
			)
			for i in range(self.n_of_tool_calls)
		]

		return ProvidersResponse(
			message="Calling all tools", reasoning="", tools_to_use=tools_to_use
		)
