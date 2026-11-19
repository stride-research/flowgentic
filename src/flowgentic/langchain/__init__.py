"""LangChain as an adapter to Flowgentic's transports, fleet and agent loop.

Flowgentic's model path speaks OpenAI Chat Completions messages, through
transports (``flowgentic.fleet.Transport``). This module joins LangChain to
it, for LangGraph graphs, prebuilt agents and LangChain tools:

- ``ChatModelTransport(chat_model)``: any LangChain chat model as a
  transport. It takes OpenAI-format messages and JSON schemas, and also
  LangChain messages and tools as they are, so a LangGraph graph's calls
  reach the model unchanged. The fleet wraps a LangChain chat model given as
  a replica in one, and the agent loop one given as its model.
- ``FleetChatModel``: the fleet as a LangChain chat model, which
  ``Fleet.chat_model()`` returns. It reads the agent's id from the run
  config's ``thread_id`` (the config passed to it, or else the LangGraph run
  it is called in; without one, the agent host's current agent) and opens no
  LangChain run of its own: the caller's config reaches the replica. Over
  LangChain replicas its messages, tools and replies pass through as they
  are; over other transports they are converted.
- ``reply_from_message`` and ``to_ai_message`` turn a LangChain ``AIMessage``
  into a ``Reply`` and back. ``inline_tool`` makes a LangChain tool one of the
  agent loop's inline tools, which the loop does for a LangChain tool it is
  given.

It needs the ``langgraph`` extra, of which ``langchain-core`` is a part.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any

try:
	from langchain_core.language_models import BaseChatModel, LanguageModelInput
	from langchain_core.messages import (
		AIMessage,
		BaseMessage,
		ToolMessage,
		convert_to_messages,
		convert_to_openai_messages,
	)
	from langchain_core.outputs import ChatResult
	from langchain_core.runnables import RunnableConfig
	from langchain_core.runnables.config import ensure_config
	from langchain_core.tools import BaseTool
	from langchain_core.utils.function_calling import convert_to_openai_tool
except ModuleNotFoundError as exc:
	if exc.name != "langchain_core":
		raise
	raise ModuleNotFoundError(
		"flowgentic.langchain needs LangChain: install flowgentic[langgraph]",
		name=exc.name,
	) from exc

from flowgentic import _agents
from flowgentic.fleet import Fleet, InvalidToolCall, Reply, ToolCall, Usage

__all__ = [
	"ChatModelTransport",
	"FleetChatModel",
	"from_openai_messages",
	"inline_tool",
	"reply_from_message",
	"to_ai_message",
	"to_openai_messages",
]


def _langchain_instrumented() -> bool:
	"""Whether OpenTelemetry's LangChain instrumentation is on in this process."""
	module = sys.modules.get("opentelemetry.instrumentation.genai.langchain")
	instrumentor = getattr(module, "LangChainInstrumentor", None)
	if instrumentor is None:
		return False
	try:
		return bool(instrumentor().is_instrumented_by_opentelemetry)
	except Exception:  # an instrumentation that changed: count it as off
		return False


def reply_from_message(message: AIMessage) -> Reply:
	"""A LangChain model's reply as a ``Reply``; ``raw`` is the message."""
	calls = tuple(
		ToolCall(c.get("id") or f"call-{n}", c["name"], c.get("args") or {})
		for n, c in enumerate(message.tool_calls)
	)
	invalid = tuple(
		InvalidToolCall(
			c.get("id") or f"invalid-{n}",
			c.get("name") or "",
			c.get("args") or "",
			c.get("error") or "the arguments did not parse",
		)
		for n, c in enumerate(getattr(message, "invalid_tool_calls", None) or ())
	)
	usage = None
	metadata = getattr(message, "usage_metadata", None)
	if metadata:
		usage = Usage(
			prompt_tokens=metadata.get("input_tokens", 0) or 0,
			completion_tokens=metadata.get("output_tokens", 0) or 0,
			cached_tokens=(metadata.get("input_token_details") or {}).get("cache_read"),
			reasoning_tokens=(metadata.get("output_token_details") or {}).get(
				"reasoning"
			),
		)
	extra = message.additional_kwargs or {}
	reasoning = extra.get("reasoning_content") or extra.get("reasoning")
	response = message.response_metadata or {}
	return Reply(
		content=message.text,
		tool_calls=calls,
		invalid_tool_calls=invalid,
		usage=usage,
		reasoning=reasoning if isinstance(reasoning, str) else None,
		finish_reason=response.get("finish_reason"),
		id=response.get("id") or message.id,
		model=response.get("model_name") or response.get("model"),
		raw=message,
	)


def to_ai_message(reply: Reply) -> AIMessage:
	"""A ``Reply`` as a LangChain ``AIMessage``: its own ``raw`` message if it
	has one. The reasoning text goes into ``response_metadata``, out of the
	conversation."""
	if isinstance(reply.raw, AIMessage):
		return reply.raw
	usage = None
	if reply.usage is not None:
		u = reply.usage
		usage = {
			"input_tokens": u.prompt_tokens,
			"output_tokens": u.completion_tokens,
			"total_tokens": u.prompt_tokens + u.completion_tokens,
		}
		if u.cached_tokens is not None:
			usage["input_token_details"] = {"cache_read": u.cached_tokens}
		if u.reasoning_tokens is not None:
			usage["output_token_details"] = {"reasoning": u.reasoning_tokens}
	metadata = {
		k: v
		for k, v in (
			("id", reply.id),
			("model_name", reply.model),
			("finish_reason", reply.finish_reason),
			("reasoning", reply.reasoning),
		)
		if v is not None
	}
	return AIMessage(
		reply.content,
		tool_calls=[
			{"name": c.name, "args": c.args, "id": c.id, "type": "tool_call"}
			for c in reply.tool_calls
		],
		invalid_tool_calls=[
			{
				"name": c.name,
				"args": c.arguments,
				"id": c.id,
				"error": c.error,
				"type": "invalid_tool_call",
			}
			for c in reply.invalid_tool_calls
		],
		usage_metadata=usage,
		response_metadata=metadata,
	)


def to_openai_messages(messages: Sequence[Any]) -> list[dict]:
	"""LangChain messages, or anything LangChain reads as messages, as
	OpenAI-format dicts."""
	return list(convert_to_openai_messages(list(messages)))


def from_openai_messages(messages: Sequence[Any]) -> list[BaseMessage]:
	"""OpenAI-format messages as LangChain messages. A tool result gets its
	tool's name, from the call it answers, as LangChain's tool messages carry
	it."""
	out = convert_to_messages(list(messages))
	names: dict[str, str] = {}
	for m in out:
		if isinstance(m, AIMessage):
			for call in m.tool_calls:
				if call.get("id"):
					names[call["id"]] = call["name"]
		elif isinstance(m, ToolMessage) and not m.name:
			m.name = names.get(m.tool_call_id)
	return out


class ChatModelTransport:
	"""A LangChain chat model as a transport; see the module's docstring.

	``tools`` are bound once per list (``bind_tools``, with ``tool_kwargs``),
	and each call's keywords, a run ``config`` included, go to the model's
	``ainvoke``. With OpenTelemetry's LangChain instrumentation on, its span is
	the call's and the fleet records none (``records_span``)."""

	langchain = True

	def __init__(self, chat_model: Any) -> None:  # noqa: ANN401  # a chat model
		self.chat_model = chat_model
		self.model = getattr(chat_model, "model_name", None) or getattr(
			chat_model, "model", None
		)
		if not isinstance(self.model, str):
			self.model = None
		self.base_url = getattr(chat_model, "openai_api_base", None)
		self._bound: dict[tuple[int, int], tuple[Any, Any, Any]] = {}

	@property
	def records_span(self) -> bool:
		return not _langchain_instrumented()

	def _bind(self, tools: Sequence[Any] | None, tool_kwargs: dict | None) -> Any:  # noqa: ANN401
		if not tools:
			return self.chat_model
		key = (id(tools), id(tool_kwargs))
		cached = self._bound.get(key)
		if cached is not None and cached[0] is tools and cached[1] is tool_kwargs:
			return cached[2]
		bound = self.chat_model.bind_tools(list(tools), **(tool_kwargs or {}))
		if len(self._bound) > 64:  # tools made per call: keep few
			self._bound.clear()
		self._bound[key] = (tools, tool_kwargs, bound)
		return bound

	async def complete(
		self,
		messages: Sequence[Any],
		tools: Sequence[Any] | None = None,
		*,
		tool_kwargs: dict | None = None,
		**kwargs: object,
	) -> Reply:
		model = self._bind(tools, tool_kwargs)
		sent = list(messages)
		if not all(isinstance(m, BaseMessage) for m in sent):
			sent = from_openai_messages(sent)
		message = await model.ainvoke(sent, **kwargs)
		return reply_from_message(message)

	def __repr__(self) -> str:
		return f"ChatModelTransport({type(self.chat_model).__name__})"


class FleetChatModel(BaseChatModel):
	"""A fleet as a LangChain chat model; see the module's docstring.

	``reserve`` and ``meta`` are the fleet's (``Fleet.ainvoke``); other
	keywords go to the replica."""

	fleet: Any
	tools: Any = None
	tool_kwargs: dict = {}

	@property
	def _llm_type(self) -> str:
		return "flowgentic-fleet"

	def bind_tools(self, tools: Sequence[Any], **kwargs: object) -> FleetChatModel:
		return FleetChatModel(fleet=self.fleet, tools=tuple(tools), tool_kwargs=kwargs)

	async def ainvoke(
		self,
		input: LanguageModelInput,
		config: RunnableConfig | None = None,
		*,
		stop: list[str] | None = None,
		**kwargs: object,
	) -> AIMessage:
		config = ensure_config(config)
		agent = config.get("configurable", {}).get("thread_id")
		agent = agent or _agents.current_agent()
		if not agent:
			raise ValueError(
				"the fleet routes by agent: pass a thread_id in the config, or run "
				"it in a LangGraph thread or an agent host"
			)
		if stop is not None:
			kwargs["stop"] = stop
		messages = self._convert_input(input).to_messages()
		fleet: Fleet = self.fleet
		if all(getattr(t, "langchain", False) for t in fleet.transports):
			reply = await fleet.ainvoke(
				str(agent),
				messages,
				tools=self.tools,
				config=config,
				tool_kwargs=self.tool_kwargs or None,
				**kwargs,
			)
		else:
			tools = (
				[convert_to_openai_tool(t) for t in self.tools] if self.tools else None
			)
			reply = await fleet.ainvoke(
				str(agent),
				to_openai_messages(messages),
				tools=tools,
				**self.tool_kwargs,
				**kwargs,
			)
		return to_ai_message(reply)

	def _generate(
		self,
		messages: list[BaseMessage],
		stop: Any = None,  # noqa: ANN401
		run_manager: Any = None,  # noqa: ANN401
		**kwargs: object,
	) -> ChatResult:
		raise NotImplementedError("the fleet is asynchronous: use ainvoke")


class _InlineTool:
	"""A LangChain tool as one of the agent loop's inline tools."""

	def __init__(self, tool: BaseTool) -> None:
		self.tool = tool
		self.name = tool.name
		self.description = tool.description
		self.args_schema = tool.tool_call_schema

	async def __call__(self, **kwargs: object) -> Any:  # noqa: ANN401
		return await self.tool.ainvoke(kwargs)


def inline_tool(tool: BaseTool) -> _InlineTool:
	"""A LangChain tool as an inline tool of the agent loop: an async callable
	with the tool's ``name``, ``description`` and ``args_schema``. Its errors,
	and an ``Interrupt``, reach the loop as the tool raises them."""
	return _InlineTool(tool)
