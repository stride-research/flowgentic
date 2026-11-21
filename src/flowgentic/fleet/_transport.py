"""Transports: how a fleet's replica, or the agent loop's model, is called.

A transport sends a conversation and the tools' JSON schemas to one model
server and returns its ``Reply``. Messages are OpenAI Chat Completions dicts
(``role``, ``content``, ``tool_calls`` with JSON-string arguments,
``tool_call_id``); a LangChain transport (``flowgentic.langchain``) also takes
LangChain messages. The reply keeps apart what the conversation keeps (its
content and tool calls, as ``Reply.message()``) and what it does not: the
model's reasoning text, its usage and the server's own response.

``HTTPTransport``, the default, calls an OpenAI-compatible server (vLLM,
SGLang, OpenRouter, OpenAI) with one httpx POST per call; ``OpenAITransport``
calls one with the ``openai`` SDK. ``parse_completion`` reads any such
server's chat completion, as the SDK's object or as its JSON.

A failed call to a server raises a ``ModelServerError``: ``ServerUnavailable``
when it did not answer, timed out, or answered with a 5xx or a 408, 409 or
429, and ``AuthenticationFailed`` for a 401, both ``transient``, so that the
fleet's ``Recovery`` waits for the server; ``RequestRejected`` for any other
4xx, the request's fault, which no wait mends.

Neither transport sends the environment's ``OPENAI_API_KEY`` to any server but
OpenAI's own API: a server on an allocation gets ``EMPTY`` unless given a key.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

__all__ = [
	"AuthenticationFailed",
	"HTTPTransport",
	"InvalidToolCall",
	"ModelServerError",
	"OpenAITransport",
	"Reply",
	"RequestRejected",
	"ServerUnavailable",
	"ToolCall",
	"Transport",
	"Usage",
	"parse_completion",
]


def _sendable(text: str) -> str:
	"""``text`` as UTF-8 can carry it: a lone surrogate, as in a file name
	Linux could not decode, written as its ``\\uXXXX`` escape, as
	``json.dumps`` with ``ensure_ascii`` writes it, so that it does not fail
	every later call of the conversation."""
	if text.isascii():
		return text
	try:
		text.encode("utf-8")
	except UnicodeEncodeError:
		return text.encode("utf-8", "backslashreplace").decode("utf-8")
	return text


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
				"function": {
					"name": c.name,
					"arguments": _sendable(json.dumps(c.args, ensure_ascii=False)),
				},
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
	``extra_body``, and returns the server's ``Reply``. A failed call raises a
	``ModelServerError``, or the client's own error: the fleet's ``Recovery``
	waits out a transient ``ModelServerError``, and knows the openai SDK's
	transient errors by name. A transport may also have ``model``, the model's name, and
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
	are an empty object. A completion with no choices raises a
	``ModelServerError``, not transient."""
	choices = _get(completion, "choices") or ()
	if not choices:
		# An error object a server sent with status 200, as some vLLM releases
		# and OpenRouter do, or an empty completion: not an empty answer. Its
		# code, if any, reads as an HTTP status: busy or a 5xx is transient.
		error = _get(completion, "error")
		said = _get(error, "message") or error or _get(completion, "message")
		code = _get(error, "code") if isinstance(error, dict) else None
		if code is None:
			code = _get(completion, "code")
		try:
			status = int(code) if code is not None else None
		except (TypeError, ValueError):
			status = None
		kind = _status_kind(status) if status is not None else ModelServerError
		raise kind(
			"the server's completion has no choices" + (f": {said}" if said else ""),
			status_code=status,
			body=completion,
		)
	choice = choices[0]
	message = _get(choice, "message")
	content = _get(message, "content")
	if not isinstance(content, str):
		content = (
			""
			if content is None
			else json.dumps(content, default=str, ensure_ascii=False)
		)
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
	# An empty ``reasoning`` does not hide ``reasoning_content``.
	reasoning = _get(message, "reasoning") or _get(message, "reasoning_content")
	if reasoning is not None and not isinstance(reasoning, str):
		reasoning = json.dumps(reasoning, default=str, ensure_ascii=False)
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


class ModelServerError(Exception):
	"""A call to a model server that failed: no answer, or an error status.

	``status_code`` is the response's, ``None`` without one; ``body`` the
	response's JSON, or its text. ``transient``: whether the server may serve
	the same call later, as one that is down, restarting, overloaded or came
	back with a new key does; the fleet's ``Recovery`` waits for those."""

	transient = False

	def __init__(
		self,
		message: str,
		*,
		status_code: int | None = None,
		body: object = None,
	) -> None:
		super().__init__(message)
		self.status_code = status_code
		self.body = body


class ServerUnavailable(ModelServerError):
	"""The server did not answer (a refused or dropped connection, a timeout),
	answered with a 5xx, or was busy: a 408 (it timed the request out), 409 or
	429 (too many requests), which the ``openai`` SDK retries too. Transient:
	the fleet's ``Recovery`` sends the call again once its check finds the
	server, after ``poll_s`` at least; it does not read a ``Retry-After``
	header, which ``body`` and ``status_code`` do not carry either."""

	transient = True


class AuthenticationFailed(ModelServerError):
	"""The server refused the key (401), as a server restarted with a new key
	does until the transport has it. Transient: ``Recovery``'s check can
	return a transport with the new key."""

	transient = True


class RequestRejected(ModelServerError):
	"""The server refused the request (a 4xx other than 401, 408, 409 and 429),
	such as a context too long or a tool schema it cannot take. Not
	transient."""


_BUSY = frozenset({408, 409, 429})
"""Statuses of a server too busy for the call now, which the ``openai`` SDK
retries: a request timeout, a conflict, too many requests."""


def _status_kind(status: int) -> type[ModelServerError]:
	"""The error for an HTTP status, or an error object's code: a 5xx or a busy
	server's is transient, a 401 too; any other 4xx is not."""
	if status >= 500 or status in _BUSY:
		return ServerUnavailable
	if status == 401:
		return AuthenticationFailed
	if status >= 400:
		return RequestRejected
	return ModelServerError


def _check_headers(headers: dict[str, str]) -> None:
	"""Refuse a header httpx cannot send: one with a line break or non-ASCII
	text. Not a server's fault, so not transient."""
	for name, value in headers.items():
		ok = isinstance(value, str) and value.isascii()
		if not ok or "\r" in value or "\n" in value:
			raise ModelServerError(
				f"the header {name!r} cannot be sent: its value must be ASCII "
				f"text on one line, not {value!r}"
			)


_OPENAI_HOST = "api.openai.com"

_UNSET: Any = object()
"""A keyword not given."""


def _timeout(httpx: Any, value: object) -> Any:  # noqa: ANN401  # httpx's Timeout
	"""httpx's timeout for ``value`` seconds, ``None`` for none, connecting within
	5 s at most, as the ``openai`` SDK does; an ``httpx.Timeout`` as it is."""
	if isinstance(value, httpx.Timeout):
		return value
	connect = 5.0 if value is None else min(float(value), 5.0)
	return httpx.Timeout(value, connect=connect)


def _api_key(api_key: str | None, base_url: object) -> str:
	"""The key a transport sends: the one given; else ``OPENAI_API_KEY``, but
	only to OpenAI's own API, as ``base_url`` (the ``openai`` SDK's default,
	``OPENAI_BASE_URL`` or OpenAI's, when it is ``None``) names it; else
	``EMPTY``. A server on an allocation, often reached over plain HTTP, is
	never sent an OpenAI key it was not given."""
	if api_key:
		return api_key
	if base_url is None:
		base_url = os.environ.get("OPENAI_BASE_URL") or f"https://{_OPENAI_HOST}/v1"
	if urlsplit(str(base_url)).hostname == _OPENAI_HOST:
		return os.environ.get("OPENAI_API_KEY") or "EMPTY"
	return "EMPTY"


def _server_message(response: Any) -> tuple[str, object]:  # noqa: ANN401  # httpx
	"""The error message of a server's response, and its JSON or text: an
	OpenAI-style ``{"error": {"message": ...}}``, vLLM's ``{"message": ...}``,
	or a web framework's ``{"detail": ...}``."""
	text = response.text
	try:
		body: object = json.loads(response.content)
	except ValueError:
		return text[:500] or response.reason_phrase, text
	if isinstance(body, dict):
		error = body.get("error")
		if isinstance(error, dict) and error.get("message"):
			return str(error["message"]), body
		if isinstance(error, str) and error:
			return error, body
		for key in ("message", "detail"):
			if body.get(key):
				return str(body[key]), body
	return text[:500], body


class HTTPTransport:
	"""An OpenAI-compatible server over httpx: vLLM, SGLang, OpenRouter or
	OpenAI. The fleet's default transport.

	Each call is one POST to ``{base_url}/chat/completions``, its reply read
	with ``parse_completion``. ``model`` is the served model's name;
	``base_url`` the server's, such as ``http://node:8000/v1``. ``api_key`` is
	sent as a bearer token; by default ``"EMPTY"``, which vLLM and SGLang
	accept when they set none, and ``OPENAI_API_KEY`` for OpenAI's own API
	(``api.openai.com``) only. ``timeout`` bounds a call, 600 s by default as
	the ``openai`` SDK's, ``None`` for none, and a connection takes at most
	5 s, as with the SDK. Other keywords are sent with every call, such as
	``temperature=0``; a call's own keywords override them. ``extra_body``'s
	keys go into the request's body and ``extra_headers``' into its headers,
	as the SDK sends them; a call's ``timeout`` replaces the transport's.

	It retries nothing: a failed call raises a ``ModelServerError``, and the
	fleet's ``Recovery`` waits out a transient one.

	**Connection pools.** A client's CPU per call grows with the calls its
	connection pool holds in flight. So the transport sends each call to its
	least busy client, and opens another client, with its own pool, when each
	holds ``calls_per_pool`` calls, 8 by default. Against a local stub, at 64
	calls in flight, 8 per pool cost 0.8 ms of CPU per call, 64 per pool
	1.5 ms; at 512, 0.8 and 1.4 ms, and one pool 29 ms. ``pools`` fixes the
	number of clients instead. A pool's connections are not capped, and all
	the pools share one SSL context. ``aclose()`` closes them once their
	calls have ended; the fleet closes a transport that ``Recovery``
	replaces.

	``client``, an ``httpx.AsyncClient``, replaces the clients the transport
	would make, and is its only one. It keeps its own timeout and headers
	unless the transport is given a ``timeout`` or an ``api_key``, and the
	transport never closes it.
	"""

	def __init__(
		self,
		model: str,
		*,
		base_url: str,
		api_key: str | None = None,
		timeout: Any = _UNSET,  # noqa: ANN401  # seconds, None, or an httpx.Timeout
		client: Any = None,  # noqa: ANN401  # an httpx.AsyncClient
		pools: int | None = None,
		calls_per_pool: int = 8,
		**params: object,
	) -> None:
		if pools is not None and pools < 1:
			raise ValueError("pools must be at least 1")
		if calls_per_pool < 1:
			raise ValueError("calls_per_pool must be at least 1")
		if client is not None and pools not in (None, 1):
			raise ValueError(
				"pass a client, or pools: the transport makes a pool's client"
			)
		parts = urlsplit(base_url)
		if parts.scheme not in ("http", "https") or not parts.netloc:
			raise ValueError(
				"base_url must be an http or https URL, such as "
				f"http://node:8000/v1, not {base_url!r}"
			)
		import httpx

		self._httpx = httpx
		self.model = model
		self.base_url = base_url.rstrip("/")
		self.url = self.base_url + "/chat/completions"
		# Where a 401 can say why: no key was given, and the environment's
		# goes to OpenAI's own API only.
		self._keyless = not api_key and parts.hostname != _OPENAI_HOST
		key = _api_key(api_key, base_url)
		self._auth = {"Authorization": f"Bearer {key}"}
		self.timeout = 600.0 if timeout is _UNSET else timeout
		self._timeout = _timeout(httpx, self.timeout)
		self.calls_per_pool = calls_per_pool
		self.params = params
		self._closing = False
		self._closer: asyncio.Task | None = None
		self._owns = client is None
		if client is not None:
			self.clients: list[Any] = [client]
			self.pools: int | None = 1
			# A given client keeps its own settings, but those given here.
			self._headers: dict[str, str] | None = self._auth if api_key else None
			if timeout is _UNSET and client.timeout != httpx.Timeout(5.0):
				self.timeout = client.timeout  # its own, which its calls get
				self._request: dict[str, Any] = {}
			else:
				# A timeout given here; or httpx's default of 5 s, which a model
				# call outlasts: the transport's 600 s, as the openai SDK does.
				self._request = {"timeout": self._timeout}
		else:
			# One SSL context for every pool: each would build its own, on the
			# event loop, about 3 ms and 0.7 MiB.
			self._ssl = httpx.create_ssl_context()
			self.clients = [self._new_client() for _ in range(pools or 1)]
			self.pools = pools
			self._headers = None  # the clients have them
			self._request = {}
		self._busy = [0] * len(self.clients)

	def _new_client(self) -> Any:  # noqa: ANN401  # an httpx.AsyncClient
		httpx = self._httpx
		return httpx.AsyncClient(
			headers=self._auth,
			timeout=self._timeout,
			verify=self._ssl,
			limits=httpx.Limits(max_connections=None, max_keepalive_connections=None),
		)

	async def aclose(self) -> None:
		"""Refuse new calls, and close the clients the transport made once the
		calls they hold have ended: it returns at once, before calls in flight
		end, and those end as they would. A client it was given stays open:
		its owner closes it."""
		self._closing = True
		if not any(self._busy):
			await self._close()

	async def _close(self) -> None:
		if self._owns:
			await asyncio.gather(*(c.aclose() for c in self.clients if not c.is_closed))

	def _ended(self) -> None:
		"""A call ended: close the clients if ``aclose`` waits for it."""
		if self._closing and not any(self._busy) and self._closer is None:
			self._closer = asyncio.get_running_loop().create_task(self._close())

	def _pick(self) -> int:
		"""The least busy client; a new one if each holds ``calls_per_pool``."""
		busy = self._busy
		i = 0 if len(busy) == 1 else min(range(len(busy)), key=busy.__getitem__)
		if busy[i] >= self.calls_per_pool and self.pools is None:
			self.clients.append(self._new_client())
			busy.append(0)
			i = len(busy) - 1
		return i

	async def complete(
		self,
		messages: Sequence[Any],
		tools: Sequence[dict] | None = None,
		**kwargs: object,
	) -> Reply:
		if self._closing:
			raise ModelServerError(f"{self.url}: the transport is closed")
		httpx = self._httpx
		params = {**self.params, **kwargs}
		extra_body = params.pop("extra_body", None)
		extra_headers = params.pop("extra_headers", None)
		request = dict(self._request)
		if "timeout" in params:
			request["timeout"] = _timeout(httpx, params.pop("timeout"))
		body = {"model": self.model, "messages": list(messages), **params}
		if tools:
			body["tools"] = list(tools)
		if extra_body:
			body.update(extra_body)
		headers = {"Content-Type": "application/json", **(self._headers or {})}
		if extra_headers:
			_check_headers(extra_headers)
			headers.update(extra_headers)
		request["headers"] = headers
		# The JSON as UTF-8 can carry it: a lone surrogate, as in a file name
		# Linux could not decode, goes as its escape, as ensure_ascii writes it.
		content = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
		content = content.encode("utf-8", "backslashreplace")
		i = self._pick()
		self._busy[i] += 1
		try:
			response = await self.clients[i].post(self.url, content=content, **request)
		except httpx.TimeoutException as exc:
			raise ServerUnavailable(
				f"{self.url}: no answer in time ({type(exc).__name__})"
			) from exc
		except (httpx.UnsupportedProtocol, httpx.LocalProtocolError) as exc:
			# Set up wrong, such as a header httpx cannot send: no wait mends it.
			raise ModelServerError(f"{self.url}: {type(exc).__name__}: {exc}") from exc
		except UnicodeEncodeError as exc:  # a header that is not ASCII
			raise ModelServerError(
				f"{self.url}: a header cannot be sent: {exc}"
			) from exc
		except httpx.TransportError as exc:
			raise ServerUnavailable(f"{self.url}: {type(exc).__name__}: {exc}") from exc
		except httpx.RequestError as exc:  # a reply httpx cannot read, redirects
			raise ModelServerError(f"{self.url}: {type(exc).__name__}: {exc}") from exc
		finally:
			self._busy[i] -= 1
			self._ended()
		status = response.status_code
		if status >= 400:
			message, payload = _server_message(response)
			if status == 401 and self._keyless:
				message += (
					" (no api_key was given: OPENAI_API_KEY is sent to "
					"api.openai.com only)"
				)
			raise _status_kind(status)(
				f"{status} {response.reason_phrase} from {self.url}: {message}",
				status_code=status,
				body=payload,
			)
		try:
			completion = json.loads(response.content)
		except ValueError as exc:
			raise ModelServerError(
				f"{self.url} answered with not a JSON chat completion: "
				f"{response.text[:200]!r}",
				status_code=status,
				body=response.text,
			) from exc
		return parse_completion(completion)

	def __repr__(self) -> str:
		return f"HTTPTransport({self.model!r}, base_url={self.base_url!r})"


class OpenAITransport:
	"""An OpenAI-compatible server through the ``openai`` SDK's ``AsyncOpenAI``:
	vLLM, SGLang, OpenRouter or OpenAI.

	``model`` is the served model's name; ``base_url`` the server's, such as
	``http://node:8000/v1``. ``api_key`` defaults to ``"EMPTY"``, which vLLM
	and SGLang accept when they set none, and to ``OPENAI_API_KEY`` for
	OpenAI's own API only: unlike the SDK alone, which sends that key to any
	server. ``timeout``
	and ``max_retries`` go to the client; pass ``max_retries=0`` to leave
	retries to the fleet's ``Recovery``. Other keywords are sent with every
	call, such as ``temperature=0`` or ``extra_body``; a call's own keywords
	override them. Needs the ``openai`` extra.

	**Connection pools.** A client's CPU cost per call grows with the calls its
	connection pool holds in flight: against a local stub, 3.5 ms per call at 64
	in flight and 24 to 31 ms at 512, plain httpx too; the same 512 calls over
	eight clients of 64 cost 3.5 ms. So the transport sends each call to its
	least busy client, and opens another client, with its own pool, when each
	holds ``calls_per_pool`` calls (64): at 512 calls in flight it has eight.
	``pools`` fixes the number of clients instead. ``client`` replaces the
	clients the transport would make, and is its only one.
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
		pools: int | None = None,
		calls_per_pool: int = 64,
		**params: object,
	) -> None:
		if pools is not None and pools < 1:
			raise ValueError("pools must be at least 1")
		if calls_per_pool < 1:
			raise ValueError("calls_per_pool must be at least 1")
		if client is not None and pools not in (None, 1):
			raise ValueError(
				"pass a client, or pools: the transport makes a pool's client"
			)
		options: dict[str, Any] = {
			"base_url": base_url,
			"api_key": _api_key(api_key, base_url),
		}
		if timeout is not None:
			options["timeout"] = timeout
		if max_retries is not None:
			options["max_retries"] = max_retries
		self._options = options
		self._owns = client is None
		self._closing = False
		self._closer: asyncio.Task | None = None
		if client is not None:
			self.clients: list[Any] = [client]
			self.pools: int | None = 1
		else:
			self.clients = [self._new_client() for _ in range(pools or 1)]
			self.pools = pools
		self.calls_per_pool = calls_per_pool
		self._busy = [0] * len(self.clients)
		self.client = self.clients[0]
		self.model = model
		self.base_url = str(getattr(self.client, "base_url", base_url) or "")
		self.params = params

	def _new_client(self) -> Any:  # noqa: ANN401  # an AsyncOpenAI
		try:
			from openai import AsyncOpenAI
		except ModuleNotFoundError as exc:
			if exc.name != "openai":
				raise
			raise ModuleNotFoundError(
				"OpenAITransport needs the openai SDK: install flowgentic[openai]",
				name=exc.name,
			) from exc
		return AsyncOpenAI(**self._options)

	def _pick(self) -> int:
		"""The least busy client; a new one if each holds ``calls_per_pool``."""
		busy = self._busy
		i = 0 if len(busy) == 1 else min(range(len(busy)), key=busy.__getitem__)
		if busy[i] >= self.calls_per_pool and self.pools is None:
			self.clients.append(self._new_client())
			busy.append(0)
			i = len(busy) - 1
		return i

	async def complete(
		self,
		messages: Sequence[Any],
		tools: Sequence[dict] | None = None,
		**kwargs: object,
	) -> Reply:
		if self._closing:
			raise ModelServerError(f"{self.base_url}: the transport is closed")
		params = {**self.params, **kwargs}
		if tools:
			params["tools"] = list(tools)
		i = self._pick()
		self._busy[i] += 1
		try:
			completion = await self.clients[i].chat.completions.create(
				model=self.model, messages=list(messages), **params
			)
		finally:
			self._busy[i] -= 1
			if self._closing and not any(self._busy) and self._closer is None:
				self._closer = asyncio.get_running_loop().create_task(self._close())
		return parse_completion(completion)

	async def aclose(self) -> None:
		"""Refuse new calls, and close the clients the transport made once the
		calls they hold have ended, as ``HTTPTransport.aclose`` does. A client
		it was given stays open: its owner closes it."""
		self._closing = True
		if not any(self._busy):
			await self._close()

	async def _close(self) -> None:
		if self._owns:
			await asyncio.gather(
				*(c.close() for c in self.clients if not c.is_closed())
			)

	def __repr__(self) -> str:
		return f"OpenAITransport({self.model!r}, base_url={self.base_url!r})"
