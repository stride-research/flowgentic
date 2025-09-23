from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from typing import Optional
import os


class ChatOpenRouter(ChatOpenAI):
	"""A ChatOpenAI instance pre-configured for OpenRouter API.

	This class encapsulates the common OpenRouter configuration pattern
	used across the codebase, providing a convenient way to create
	ChatOpenAI instances with OpenRouter settings.

	Example:
		llm = ChatOpenRouter(model="google/gemini-2.5-flash")
	"""

	def __init__(self, model: str, api_key: Optional[str] = None, **kwargs):
		"""Initialize ChatOpenRouter with OpenRouter configuration.

		Args:
			model: The model name to use (e.g., "google/gemini-2.5-flash")
			api_key: OpenRouter API key. If None, will use OPEN_ROUTER_API_KEY env var
			**kwargs: Additional arguments passed to ChatOpenAI
		"""
		api_key = api_key or os.getenv("OPEN_ROUTER_API_KEY")
		if not api_key:
			raise ValueError(
				"OpenRouter API key not provided. Set OPEN_ROUTER_API_KEY environment variable "
				"or pass api_key parameter."
			)

		super().__init__(
			model=model,
			openai_api_base="https://openrouter.ai/api/v1",
			openai_api_key=api_key,
			**kwargs,
		)


def ChatLLMProvider(provider: str, *args, **kwargs) -> BaseChatModel:
	"""Simple factory to simplify provider import"""
	provider_lower = provider.lower()
	if provider_lower == "openrouter":
		return ChatOpenRouter(*args, **kwargs)
	elif provider_lower == "chatgpt" or provider_lower == "chatopenai":
		return ChatOpenAI(*args, **kwargs)
	elif provider_lower == "ollama":
		return ChatOllama(*args, **kwargs)
