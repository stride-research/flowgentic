"""Transports: how a fleet's replica, or the agent loop's model, is called.

A transport sends a conversation and the tools' JSON schemas to one model
server and returns its ``Reply``. Messages are OpenAI Chat Completions dicts
(``role``, ``content``, ``tool_calls`` with JSON-string arguments,
``tool_call_id``); a LangChain transport (``flowgentic.langchain``) also takes
LangChain messages. The reply keeps apart what the conversation keeps (its
content and tool calls, as ``Reply.message()``) and what it does not: the
model's reasoning text, its usage and the server's own response.

``OpenAITransport`` calls an OpenAI-compatible server (vLLM, SGLang,
OpenRouter, OpenAI) with the ``openai`` SDK. ``parse_completion`` reads any
such server's chat completion, as the SDK's object or as its JSON.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
	"InvalidToolCall",
	"OpenAITransport",
	"Reply",
	"ToolCall",
	"Transport",
	"Usage",
	"parse_completion",
]


@dataclass(frozen=True, slots=True)
class ToolCall:
	"""A tool call whose arguments parsed as a JSON object."""

	id: str
	name: str
	args: dict


@dataclass(frozen=True, slots=True)
class InvalidToolCall:
	"""A tool call whose arguments did not parse: the model reads ``error``."""

	id: str
	name: str
	arguments: str
	error: str


@dataclass(frozen=True, slots=True)
class Usage:
	"""What the server counted. ``cached_tokens`` (prompt tokens it found in its
	prefix cache) and ``reasoning_tokens`` (completion tokens spent thinking)
	are ``None`` when it did not say."""

	prompt_tokens: int = 0
	completion_tokens: int = 0
	cached_tokens: int | None = None
	reasoning_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class Reply:
	"""A model's reply. ``reasoning`` is the thinking the server split out of
	the answer; it stays out of the conversation. ``raw`` is the transport's
	own response: the SDK's completion, or a LangChain message."""

	content: str = ""
	tool_calls: tuple[ToolCall, ...] = ()
	invalid_tool_calls: tuple[InvalidToolCall, ...] = ()
	usage: Usage | None = None
	reasoning: str | None = None
	finish_reason: str | None = None
	id: str | None = None
	model: str | None = None
	raw: Any = field(default=None, repr=False, compare=False)

	def message(self) -> dict:
		"""The reply as the conversation keeps it: an assistant message with its
		content and tool calls, without the reasoning. A call whose arguments
		did not parse keeps its id, with ``{}`` as arguments: servers parse the
		arguments of the calls they are sent, and the model reads why its own
		were refused in that call's result."""
		message: dict = {"role": "assistant", "content": self.content}
		calls = [
			{
				"id": c.id,
				"type": "function",
				"function": {"name": c.name, "arguments": json.dumps(c.args)},
			}
			for c in self.tool_calls
		]
		calls.extend(
			{
				"id": c.id,
				"type": "function",
				"function": {"name": c.name, "arguments": "{}"},
			}
			for c in self.invalid_tool_calls
		)
		if calls:
			message["tool_calls"] = calls
		return message


@runtime_checkable
class Transport(Protocol):
	"""Calls one model server.

	``complete`` sends ``messages`` with the tools' JSON schemas, ``tools``
	(``None`` for none), and per-call keywords such as ``max_tokens`` or
	``extra_body``, and returns the server's ``Reply``. It raises the client's
	own errors: the fleet's ``Recovery`` knows the OpenAI client's transient
	ones by name. A transport may also have ``model``, the model's name, and
	``base_url``, the server's, which the ``chat`` span records."""

	async def complete(
		self,
		messages: Sequence[Any],
		tools: Sequence[dict] | None = None,
		**kwargs: object,
	) -> Reply: ...


def _get(obj: object, key: str) -> Any:  # noqa: ANN401  # JSON or SDK objects
	"""A field of a JSON object or of the SDK's model, its extra fields
	included; ``None`` if absent."""
	if obj is None:
		return None
	if isinstance(obj, dict):
		return obj.get(key)
	value = getattr(obj, key, None)
	if value is None:
		extra = getattr(obj, "model_extra", None)
		if extra:
			value = extra.get(key)
	return value


def _int(value: object) -> int | None:
	return None if value is None else int(value)


def parse_completion(completion: object) -> Reply:
	"""A ``Reply`` from an OpenAI-compatible chat completion: the ``openai``
	SDK's ``ChatCompletion``, or its JSON as a dict.

	The reasoning text is the message's ``reasoning`` (vLLM since it renamed
	the field, OpenRouter) or ``reasoning_content`` (older vLLM, SGLang).
	Cached prompt tokens come from ``usage.prompt_tokens_details.cached_tokens``
	and reasoning tokens from ``usage.completion_tokens_details
	.reasoning_tokens``, when the server sends them. A tool call's arguments
	that are not a JSON object make an ``InvalidToolCall``; empty arguments
	are an empty object."""
	choices = _get(completion, "choices") or ()
	choice = choices[0] if choices else None
	message = _get(choice, "message")
	content = _get(message, "content")
	if not isinstance(content, str):
		content = "" if content is None else json.dumps(content, default=str)
	calls: list[ToolCall] = []
	invalid: list[InvalidToolCall] = []
	for n, call in enumerate(_get(message, "tool_calls") or ()):
		function = _get(call, "function")
		name = _get(function, "name") or ""
		arguments = _get(function, "arguments")
		call_id = _get(call, "id") or f"call-{n}"
		if arguments is None or (isinstance(arguments, str) and not arguments.strip()):
			calls.append(ToolCall(call_id, name, {}))
			continue
		if isinstance(arguments, dict):
			calls.append(ToolCall(call_id, name, arguments))
			continue
		try:
			args = json.loads(arguments)
		except (TypeError, ValueError) as exc:
			invalid.append(
				InvalidToolCall(call_id, name, str(arguments), f"not valid JSON: {exc}")
			)
			continue
		if isinstance(args, dict):
			calls.append(ToolCall(call_id, name, args))
		else:
			invalid.append(
				InvalidToolCall(call_id, name, arguments, "not a JSON object")
			)
	reasoning = _get(message, "reasoning")
	if reasoning is None:
		reasoning = _get(message, "reasoning_content")
	if reasoning is not None and not isinstance(reasoning, str):
		reasoning = json.dumps(reasoning, default=str)
	usage = None
	raw_usage = _get(completion, "usage")
	if raw_usage is not None:
		usage = Usage(
			prompt_tokens=_int(_get(raw_usage, "prompt_tokens")) or 0,
			completion_tokens=_int(_get(raw_usage, "completion_tokens")) or 0,
			cached_tokens=_int(
				_get(_get(raw_usage, "prompt_tokens_details"), "cached_tokens")
			),
			reasoning_tokens=_int(
				_get(_get(raw_usage, "completion_tokens_details"), "reasoning_tokens")
			),
		)
	return Reply(
		content=content,
		tool_calls=tuple(calls),
		invalid_tool_calls=tuple(invalid),
		usage=usage,
		reasoning=reasoning or None,
		finish_reason=_get(choice, "finish_reason"),
		id=_get(completion, "id"),
		model=_get(completion, "model"),
		raw=completion,
	)


class OpenAITransport:
	"""An OpenAI-compatible server through the ``openai`` SDK's ``AsyncOpenAI``:
	vLLM, SGLang, OpenRouter or OpenAI.

	``model`` is the served model's name; ``base_url`` the server's, such as
	``http://node:8000/v1``. ``api_key`` defaults to ``OPENAI_API_KEY``, then
	to ``"EMPTY"``, which vLLM and SGLang accept when they set none. ``timeout``
	and ``max_retries`` go to the client; pass ``max_retries=0`` to leave
	retries to the fleet's ``Recovery``. Other keywords are sent with every
	call, such as ``temperature=0`` or ``extra_body``; a call's own keywords
	override them. ``client`` replaces the client the transport would make.

	``pools`` makes that many clients, each with its own connection pool, and
	sends the calls to them in turn. One client's cost per call grows with the
	calls it holds in flight: against a stub server, 31 ms of CPU per call at
	512 in flight with one pool, plain httpx too, and 3.5 ms with eight; at 64
	in flight, 3.5 ms either way. Give a replica that holds hundreds of calls
	at once ``pools=8``. Needs the ``openai`` extra.
	"""

	def __init__(
		self,
		model: str,
		*,
		base_url: str | None = None,
		api_key: str | None = None,
		timeout: float | None = None,
		max_retries: int | None = None,
		client: Any = None,  # noqa: ANN401  # an AsyncOpenAI
		pools: int = 1,
		**params: object,
	) -> None:
		if pools < 1:
			raise ValueError("pools must be at least 1")
		if client is not None and pools != 1:
			raise ValueError(
				"pass a client, or pools: the transport makes a pool's client"
			)
		clients = [client] if client is not None else []
		for _ in range(pools if client is None else 0):
			try:
				from openai import AsyncOpenAI
			except ModuleNotFoundError as exc:
				if exc.name != "openai":
					raise
				raise ModuleNotFoundError(
					"OpenAITransport needs the openai SDK: install flowgentic[openai]",
					name=exc.name,
				) from exc
			options: dict[str, Any] = {}
			if timeout is not None:
				options["timeout"] = timeout
			if max_retries is not None:
				options["max_retries"] = max_retries
			clients.append(
				AsyncOpenAI(
					base_url=base_url,
					api_key=api_key or os.environ.get("OPENAI_API_KEY") or "EMPTY",
					**options,
				)
			)
		self.clients = clients
		self.client = clients[0]
		self._next = 0
		self.model = model
		self.base_url = str(getattr(self.client, "base_url", base_url) or "")
		self.params = params

	async def complete(
		self,
		messages: Sequence[Any],
		tools: Sequence[dict] | None = None,
		**kwargs: object,
	) -> Reply:
		params = {**self.params, **kwargs}
		if tools:
			params["tools"] = list(tools)
		client = self.client
		if len(self.clients) > 1:
			client = self.clients[self._next]
			self._next = (self._next + 1) % len(self.clients)
		completion = await client.chat.completions.create(
			model=self.model, messages=list(messages), **params
		)
		return parse_completion(completion)

	def __repr__(self) -> str:
		return f"OpenAITransport({self.model!r}, base_url={self.base_url!r})"
