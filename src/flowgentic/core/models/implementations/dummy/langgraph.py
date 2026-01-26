import uuid
from typing import Any, List, Optional, Dict
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.callbacks import CallbackManagerForLLMRun


class DummyLanggraphModelProvider(BaseChatModel):
	"""
	A dummy LLM that always triggers tool calls.
	Used for testing HPC engine concurrency and routing.
	"""

	fixed_tool_names: List[str] = []
	calls_per_tool: int = 1

	@property
	def _llm_type(self) -> str:
		return "dummy-hpc-mocker"

	def bind_tools(self, tools: List[Any], **kwargs: Any) -> Any:
		# Extract names from the tools (whether they are functions or LC tools)
		self.fixed_tool_names = [
			getattr(t, "name", getattr(t, "__name__", str(t))) for t in tools
		]
		return self

	def _generate(
		self,
		messages: List[BaseMessage],
		stop: Optional[List[str]] = None,
		run_manager: Optional[CallbackManagerForLLMRun] = None,
		**kwargs: Any,
	) -> ChatResult:
		last_message = messages[-1]
		if last_message.type == "tool":
			return ChatResult(
				generations=[
					ChatGeneration(
						message=AIMessage(content="I have finished the HPC tasks.")
					)
				]
			)

		tool_calls = []
		for name in self.fixed_tool_names:
			for _ in range(self.calls_per_tool):
				tool_calls.append(
					{
						"name": name,
						"args": {},  # Empty args as requested
						"id": f"call_{uuid.uuid4().hex[:8]}",
						"type": "tool_call",
					}
				)

		message = AIMessage(content="", tool_calls=tool_calls)
		return ChatResult(generations=[ChatGeneration(message=message)])

	async def _agenerate(self, *args, **kwargs) -> ChatResult:
		# For simplicity in this dummy, just use the sync version
		return self._generate(*args, **kwargs)
