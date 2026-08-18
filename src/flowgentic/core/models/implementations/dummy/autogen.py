import uuid
from typing import Any, Dict, List, Optional

from autogen import AssistantAgent


class DummyAutoGenClient:
	"""
	A dummy AutoGen model client that returns deterministic responses.
	Always triggers tool calls when tools are available, otherwise returns simple text.
	"""

	def __init__(self, config=None, **kwargs):
		"""
		Initialize the dummy client.
		AutoGen passes config as first positional argument, then kwargs.
		"""
		# Merge config dict if provided
		if isinstance(config, dict):
			kwargs.update(config)
		self.model = kwargs.get("model", "dummy-model")
		self.calls_per_tool = kwargs.get("calls_per_tool", 1)
		self.fixed_tool_names = []

	def create(self, params: Dict[str, Any]) -> Any:
		"""
		Create a chat completion response.
		Returns a response object that implements ModelClientResponseProtocol.
		"""
		messages = params.get("messages", [])
		tools = params.get("tools", [])

		# Extract tool names if available
		if tools:
			self.fixed_tool_names = [
				tool.get("function", {}).get("name", tool.get("name", ""))
				for tool in tools
				if tool.get("function", {}).get("name") or tool.get("name")
			]

		# Check if last message is a tool result
		last_message = messages[-1] if messages else {}
		if last_message.get("role") == "tool" or any(
			msg.get("role") == "tool" for msg in messages[-3:]
		):
			# Return a simple completion after tool execution
			return DummyResponse(
				content="I have finished the HPC tasks. TERMINATE", model=self.model
			)

		# If tools are available, return tool calls
		if self.fixed_tool_names:
			tool_calls = []
			for _ in range(self.calls_per_tool):
				for name in self.fixed_tool_names:
					tool_calls.append(
						{
							"id": f"call_{uuid.uuid4().hex[:8]}",
							"type": "function",
							"function": {
								"name": name,
								"arguments": "{}",  # Empty args as requested
							},
						}
					)

			return DummyResponse(content="", tool_calls=tool_calls, model=self.model)

		# Default: return a simple message
		return DummyResponse(content="I understand. TERMINATE", model=self.model)

	def message_retrieval(self, response: Any) -> List[Dict[str, Any]]:
		"""
		Extract messages from the response.
		Returns a list of message dictionaries.
		"""
		messages = []
		if hasattr(response, "choices") and response.choices:
			choice = response.choices[0]
			message = {
				"role": "assistant",
				"content": choice.message.get("content", ""),
			}
			if choice.message.get("tool_calls"):
				message["tool_calls"] = choice.message["tool_calls"]
			messages.append(message)
		return messages

	def cost(self, response: Any) -> float:
		"""Return the cost of the response (always 0 for dummy)."""
		return 0.0

	def get_usage(self, response: Any) -> Dict[str, Any]:
		"""
		Return usage statistics.
		Must include: prompt_tokens, completion_tokens, total_tokens, cost, model
		"""
		return {
			"prompt_tokens": 10,
			"completion_tokens": 5,
			"total_tokens": 15,
			"cost": 0.0,
			"model": self.model,
		}


class DummyResponse:
	"""
	A simple response object that implements ModelClientResponseProtocol.
	"""

	def __init__(
		self, content: str, model: str, tool_calls: Optional[List[Dict]] = None
	):
		self.model = model
		self.choices = [DummyChoice(content, tool_calls)]

	def __getattr__(self, name: str) -> Any:
		"""Handle any other attributes that might be accessed."""
		return None


class DummyChoice:
	"""A choice object within the response."""

	def __init__(self, content: str, tool_calls: Optional[List[Dict]] = None):
		self.message = {
			"role": "assistant",
			"content": content,
		}
		if tool_calls:
			self.message["tool_calls"] = tool_calls


def create_assistant_with_dummy_model(
	name: str,
	system_message: str,
	model: str = "dummy-model",
	calls_per_tool: int = 1,
	**kwargs,
) -> AssistantAgent:
	"""
	Helper function to create an AssistantAgent with DummyAutoGenClient.
	This handles all the setup needed for using the dummy model client.

	Args:
	    name: Name of the assistant agent
	    system_message: System message for the agent
	    model: Model name (default: "dummy-model")
	    **kwargs: Additional arguments to pass to AssistantAgent

	Returns:
	    AssistantAgent configured with DummyAutoGenClient
	"""
	from autogen.oai.client import OpenAIWrapper

	# Set up config with dummy model client
	model_config = {
		"model": model,
		"model_client_cls": "DummyAutoGenClient",
		"calls_per_tool": calls_per_tool,
	}

	llm_config = {
		"config_list": [model_config],
		"temperature": 0,
	}

	# Create agent without LLM config first to avoid AutoGen creating its own client
	agent = AssistantAgent(
		name=name, llm_config=False, system_message=system_message, **kwargs
	)

	# Create client wrapper and register the dummy model client
	client = OpenAIWrapper(**llm_config)
	client.register_model_client(model_client_cls=DummyAutoGenClient)

	# Verify registration worked
	if not any(isinstance(c, DummyAutoGenClient) for c in client._clients):
		raise RuntimeError("Failed to register DummyAutoGenClient")

	# Set the llm_config and client on the agent
	agent.llm_config = llm_config
	agent.client = client

	return agent
