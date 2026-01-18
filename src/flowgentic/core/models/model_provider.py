from flowgentic.core.schemas.prompt_input import PromptInput
from abc import ABC, abstractmethod
from flowgentic.core.schemas.prompt_input import PromptInput
from flowgentic.core.schemas.providers_response import ProvidersResponse


class ModelProvider(ABC):
	"""
	A framework-agnostic interface for LLM calls.
	Users can extend this to wrap any client (LangChain, OpenAI, Ollama, etc.)
	"""

	def __init__(self, model_id: str, **config):
		self.model_id = model_id
		self.config = config

	def _prepare_messages(self, prompt_input: PromptInput, memory_context: str) -> list:
		"""Standardizes how instructions and context are merged."""
		system_content = f"{memory_context}\n\n{prompt_input.system_input}".strip()
		return [
			{"role": "system", "content": system_content},
			{"role": "user", "content": prompt_input.user_input},
		]

	@abstractmethod
	async def aprompt(
		self, prompt_input: PromptInput, memory_context: str, **kwargs
	) -> ProvidersResponse:
		"""Async version of the prompt call."""
		pass
