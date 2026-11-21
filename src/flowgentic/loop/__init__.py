"""Flowgentic's own agent loop: a runner for the agent host, on OpenAI-format
messages, without LangChain or LangGraph.

``LoopRunner(model, tools)`` runs tool-calling agents in a
``flowgentic.host.AgentHost``. The model replies; the tool calls of its reply
run, all at once; their results go back to the model; and so on until the
model answers. It is the agent that ``GraphRunner`` runs with a ReAct graph,
written as a ``while`` loop. ``GraphRunner`` stays, for graphs and for what
LangGraph adds: streaming, middleware, human-in-the-loop resumes.

- **Messages** are OpenAI Chat Completions dicts: ``role``, ``content``,
  ``tool_calls`` with JSON-string arguments, ``tool_call_id``. The output's
  messages, the state a suspension saves and ``trim`` all use them.
- **The model** is a ``flowgentic.fleet.Fleet``, which routes and admits each
  call by agent, or a transport (``flowgentic.fleet.Transport``), such as
  ``HTTPTransport``. Each model call is a GenAI ``chat`` span with its usage.
  A LangChain chat model, or the fleet's ``FleetChatModel``, is accepted too,
  through LangChain's adapter (``flowgentic.langchain``).
- **Tools** are ``flowgentic.tools`` declarations, which run on a
  ``ToolRuntime`` (task tools as RHAPSODY tasks, MCP tools through the tool
  service), and inline tools: plain functions, ``async`` or not, which run in
  the agent's process. A LangChain tool runs inline through the adapter. A
  tool's JSON schema comes from its pydantic ``args_schema``, or from the
  function's signature through pydantic. The calls of one reply run
  concurrently.
- **Suspension.** A task tool that suspends makes the loop save the agent's
  state, its messages and its count of model calls, to a ``KeyValueStore``
  with one put, and return ``Suspended``. The host awaits the task; the loop
  reads the state back with one get and goes on. The entry is deleted when the
  agent ends, however it ends, so the store holds the agents that are
  suspended or running since a suspension. A suspended agent holds no memory
  in the process.
- **Repeat safety.** A task or MCP call is keyed by the agent, the turn (the
  number of the model call that made it) and the call's id. On resume the
  suspended task is found again, not run again; a later turn that reuses a call
  id, as a server that numbers its calls per reply does, makes a new call.
- **Errors go to the model.** A tool that raises, arguments its schema
  refuses, a tool that does not exist, arguments that are not valid JSON, a
  failed task or MCP call: each becomes a tool result that says so. A runtime
  set up wrong, without the session or the service client a tool needs, fails
  the agent instead.
- **A limit on model calls.** ``max_model_calls`` counts over the agent's whole
  run, suspensions included; the call past it fails the agent with
  ``ModelCallLimitExceeded``.
- **Structured answers.** With ``response_format``, a pydantic model, the model
  answers by calling a tool of that model's name, on its own in its reply; the
  arguments are validated, and a refusal goes back to the model. A plain reply
  that parses as the model's JSON is accepted too; any other plain reply is
  answered with a request to call the tool.
- **Trimming.** ``trim(messages)``, a function or a coroutine function, is
  called before each model call. What it returns is the agent's history from
  then on: what the model sees, what a suspension saves, what the output
  holds. It must keep each tool call with its result.
- **Interrupts.** An inline tool that raises ``Interrupt(value)`` stops its
  agent: the host ends it as interrupted, with an ``Interruption`` that holds
  the value, the call and the history. The store keeps nothing of it.
- **Accounting and telemetry.** The agent's states are counted as the host's
  other parts count them: model calls (through the fleet, or around a
  transport's call), waits on tasks, calls to the tool service, inline tools
  as working. Task and MCP calls are ``execute_tool`` spans under the agent's
  ``invoke_agent`` span, and each suspension is the host's
  ``flowgentic.agent.suspend`` event.

An agent's input is a string, a message (a dict with a ``role``), a list of
messages or a dict with ``messages``, as a LangGraph graph's input; LangChain
messages are converted. Anything else fails the agent with ``TypeError``.
``Agent.config`` is not read. A finished agent's output is a ``LoopOutput``.
The runner never changes the caller's messages.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sys
import time
import typing
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, ValidationError, create_model

from flowgentic import _agents, _telemetry
from flowgentic._agents import AgentState, ToolSetupError
from flowgentic.fleet import Fleet, Reply, _as_transport, _complete
from flowgentic.host import Agent, Finished, Interrupted, Step, Suspended
from flowgentic.store import KeyValueStore, MemoryStore
from flowgentic.tools import McpTool, TaskTool, ToolRuntime

__all__ = [
	"Interrupt",
	"Interruption",
	"LoopOutput",
	"LoopRunner",
	"ModelCallLimitExceeded",
]

log = logging.getLogger(__name__)

# What the model reads when a call fails, as LangGraph's ToolNode words it.
ERROR = "Error: {error}\n Please fix your mistakes."
NOT_A_TOOL = "Error: {name} is not a valid tool, try one of [{names}]."
ANSWERED = "Answer recorded."

Trim = Callable[[list[dict]], Sequence[dict] | Awaitable[Sequence[dict]]]


@dataclass(frozen=True, slots=True)
class LoopOutput:
	"""A finished agent's output: its messages, the validated answer when the
	runner has a ``response_format``, and its model calls."""

	messages: list[dict]
	structured_response: Any = None
	model_calls: int = 0


@dataclass(frozen=True, slots=True)
class Interruption:
	"""Why an agent stopped: the value its tool raised ``Interrupt`` with, the
	tool call (in the reply's ``tool_calls`` format), and the agent's messages
	then, in which that call has no result."""

	value: Any
	tool_call: dict
	messages: list[dict]
	model_calls: int


class Interrupt(Exception):  # noqa: N818  # a signal, as LangGraph's interrupt
	"""Raised by an inline tool to stop its agent; the host ends the agent as
	interrupted, with an ``Interruption`` that carries ``value``."""

	def __init__(self, value: object = None) -> None:
		super().__init__(value)
		self.value = value


class ModelCallLimitExceeded(RuntimeError):  # noqa: N818
	"""An agent needed more model calls than its runner's ``max_model_calls``."""


@dataclass(frozen=True, slots=True)
class _Call:
	"""One tool call of a reply. ``args`` is ``None`` when the model's
	arguments did not parse, and ``error`` says why."""

	id: str
	name: str
	args: dict | None
	error: str | None
	raw: dict


@dataclass(frozen=True, slots=True)
class _Inline:
	"""A function the loop runs in the agent's process."""

	name: str
	description: str
	schema: Any
	fn: Callable[..., Any]
	with_call_id: bool = False  # called with ``_call_id``, the call's id
	is_async: bool = True  # else run in a thread, off the event loop


class _Result:
	"""What one tool call gave: a result for the model (an error or not), a task
	to wait on, an interrupt, or a structured answer."""

	__slots__ = ("message", "error", "wait", "interrupt", "answer")

	def __init__(
		self,
		message: dict | None = None,
		error: bool = False,
		wait: tuple[str, str] | None = None,
		interrupt: Interrupt | None = None,
		answer: BaseModel | None = None,
	) -> None:
		self.message = message
		self.error = error
		self.wait = wait
		self.interrupt = interrupt
		self.answer = answer


class LoopRunner:
	"""Runs tool-calling agents; see the module's docstring.

	``model``: a ``Fleet``, a transport, or a LangChain chat model.
	``tools``: ``task_tool`` and ``mcp_tool`` declarations, which need
	``runtime``, and inline tools. ``store``: where suspended agents' state
	goes, a ``MemoryStore`` by default; each agent's key is ``key_prefix`` and
	its id. ``system_prompt`` goes first in every model call and is never saved
	or trimmed. ``model_kwargs`` go with every model call, such as
	``max_tokens``. ``response_format``, ``max_model_calls`` and ``trim``: see
	the module's docstring.
	"""

	def __init__(
		self,
		model: object,
		tools: Iterable[object] = (),
		*,
		runtime: ToolRuntime | None = None,
		store: KeyValueStore | None = None,
		system_prompt: str | None = None,
		response_format: type[BaseModel] | None = None,
		max_model_calls: int = 25,
		trim: Trim | None = None,
		model_kwargs: dict | None = None,
		key_prefix: str = "loop/",
	) -> None:
		if max_model_calls < 1:
			raise ValueError("max_model_calls must be at least 1")
		fleet = model if isinstance(model, Fleet) else getattr(model, "fleet", None)
		self._fleet: Fleet | None = fleet if isinstance(fleet, Fleet) else None
		self._transport = None if self._fleet is not None else _as_transport(model)
		self.runtime = runtime
		self.store = store if store is not None else MemoryStore()
		self.system_prompt = system_prompt
		self.response_format = response_format
		self.max_model_calls = max_model_calls
		self.trim = trim
		self.model_kwargs = dict(model_kwargs or {})
		self.key_prefix = key_prefix
		# In each task's turn, so that two runners in one process, whose agents
		# may share ids and call ids, never share a task, through a runtime or
		# the host's registrations.
		self._token = uuid.uuid4().hex[:8]
		self._system = (
			None
			if system_prompt is None
			else {"role": "system", "content": system_prompt}
		)

		self._tools: dict[str, TaskTool | McpTool | _Inline] = {}
		self._arg_schemas: dict[str, Any] = {}
		schemas: list[dict] = []
		for tool in tools:
			if isinstance(tool, (TaskTool, McpTool)):
				if runtime is None:
					raise ValueError(
						f"{tool.name} is a task or MCP tool: the runner needs a "
						"ToolRuntime (runtime=...)"
					)
				if isinstance(tool, TaskTool):
					runtime.check(tool)  # set up wrong: fails here, not in an agent
				schema = tool.args_schema
				if schema is None and isinstance(tool, TaskTool):
					schema = _signature_schema(tool.name, tool.fn)
				name, description, runs = tool.name, tool.description, tool
			else:
				runs = _inline(tool)
				name, description, schema = runs.name, runs.description, runs.schema
			if name in self._tools:
				raise ValueError(f"two tools are named {name!r}")
			self._tools[name] = runs
			self._arg_schemas[name] = schema
			schemas.append(_tool_schema(name, description, schema))
		self._answer: str | None = None
		if response_format is not None:
			self._answer = response_format.__name__
			if self._answer in self._tools:
				raise ValueError(
					f"a tool is named {self._answer!r}, as the response format is"
				)
			description = (response_format.__doc__ or "").strip() or self._answer
			schemas.append(_tool_schema(self._answer, description, response_format))
		self.tool_schemas: list[dict] = schemas

		self._saved: set[str] = set()  # agents with an entry in the store
		self.model_calls = 0
		self.tool_calls = 0
		self.tool_errors = 0
		self.suspensions = 0
		self.interrupts = 0
		self.store_puts = 0
		self.store_gets = 0
		self.store_deletes = 0
		self.delete_failures = 0

	def key(self, agent_id: str) -> str:
		"""The store key of an agent's saved state."""
		return f"{self.key_prefix}{agent_id}"

	# --- the Runner protocol ---------------------------------------------------------

	async def start(self, agent: Agent) -> Step:
		"""Run the agent from its input until it ends, suspends or stops."""
		return await self._run(agent.id, _messages(agent.input), 0)

	async def resume(self, agent: Agent, done: dict[str, Any]) -> Step:
		"""Read the agent's state back and run it on; its tasks have ended."""
		state = self.store.get(self.key(agent.id), None)
		self.store_gets += 1
		if state is None:
			raise RuntimeError(
				f"agent {agent.id!r} has no saved state under {self.key(agent.id)!r}"
			)
		return await self._run(agent.id, state["messages"], state["model_calls"])

	def stats(self) -> dict:
		"""Model calls, tool calls and their errors, suspensions, interrupts, and
		the calls made to the store."""
		return {
			"model_calls": self.model_calls,
			"tool_calls": self.tool_calls,
			"tool_errors": self.tool_errors,
			"suspensions": self.suspensions,
			"interrupts": self.interrupts,
			"store_puts": self.store_puts,
			"store_gets": self.store_gets,
			"store_deletes": self.store_deletes,
			"delete_failures": self.delete_failures,
		}

	# --- the loop --------------------------------------------------------------------

	async def _run(self, agent_id: str, messages: list[dict], calls: int) -> Step:
		try:
			step = await self._loop(agent_id, messages, calls)
		except BaseException:
			self._end(agent_id)
			raise
		if not isinstance(step, Suspended):
			self._end(agent_id)
		return step

	async def _loop(self, agent_id: str, messages: list[dict], calls: int) -> Step:
		i, made, pending = _pending(messages)
		while True:
			if pending:
				step = await self._tools_turn(
					agent_id, messages, calls, i, made, pending
				)
				if step is not None:
					return step
			if calls >= self.max_model_calls:
				raise ModelCallLimitExceeded(
					f"agent {agent_id!r} reached its limit of {calls} model calls "
					"without an answer"
				)
			if self.trim is not None:
				messages = await self._trimmed(messages)
			reply = await self._ask(agent_id, messages)
			calls += 1
			self.model_calls += 1
			messages.append(reply.message())
			i, made = len(messages) - 1, _calls_of(reply, messages[-1])
			pending = made
			if made:
				continue
			if self._answer is None:
				return Finished(LoopOutput(messages, None, calls))
			answer = self._parse(reply)
			if answer is not None:
				return Finished(LoopOutput(messages, answer, calls))
			nudge = f"Answer by calling the {self._answer} tool, on its own."
			messages.append({"role": "user", "content": nudge})

	async def _trimmed(self, messages: list[dict]) -> list[dict]:
		out = self.trim(messages)
		if inspect.isawaitable(out):
			out = await out
		return list(out)

	async def _ask(self, agent_id: str, messages: list[dict]) -> Reply:
		sent = messages if self._system is None else [self._system, *messages]
		tools = self.tool_schemas or None
		if self._fleet is not None:
			# the fleet notes the reply's tool calls for the tasks' records
			return await self._fleet.ainvoke(
				agent_id, sent, tools=tools, **self.model_kwargs
			)
		with _agents.activity(AgentState.MODEL_REQUEST):
			reply = await _complete(self._transport, sent, tools, self.model_kwargs)
		# when the agent committed to the reply's tool calls
		_agents.note_reply(
			agent_id, time.time(), [c.id for c in reply.tool_calls if c.id]
		)
		return reply

	def _parse(self, reply: Reply) -> BaseModel | None:
		try:
			return self.response_format.model_validate_json(reply.content)
		except ValidationError:
			return None

	async def _tools_turn(
		self,
		agent_id: str,
		messages: list[dict],
		calls: int,
		i: int,
		made: list[_Call],
		pending: list[_Call],
	) -> Step | None:
		scope = f"{self._token}-{calls}"  # the same when the agent resumes
		alone = len(made) == 1
		if len(pending) == 1:
			results = [await self._call(agent_id, scope, pending[0], alone)]
		else:
			tasks = [
				asyncio.ensure_future(self._call(agent_id, scope, call, alone))
				for call in pending
			]
			try:
				results = await asyncio.gather(*tasks)
			except BaseException:
				# One call failed the agent: stop the others.
				for task in tasks:
					task.cancel()
				await asyncio.gather(*tasks, return_exceptions=True)
				raise
		waits: list[tuple[str, str]] = []
		stops: list[Interruption] = []
		answer = None
		for call, result in zip(pending, results, strict=True):
			if result.message is not None:
				messages.append(result.message)
				self.tool_calls += 1
				if result.error:
					self.tool_errors += 1
			if result.wait is not None:
				waits.append(result.wait)
			if result.interrupt is not None:
				stops.append(
					Interruption(result.interrupt.value, call.raw, messages, calls)
				)
			if result.answer is not None:
				answer = result.answer
		_order(messages, i, made)
		if stops:
			self.interrupts += 1
			return Interrupted(tuple(stops))
		if waits:
			self._save(agent_id, messages, calls)
			return Suspended(tuple(waits))
		if answer is not None:
			return Finished(LoopOutput(messages, answer, calls))
		return None

	# --- one tool call ---------------------------------------------------------------

	async def _call(
		self, agent_id: str, scope: str, call: _Call, alone: bool
	) -> _Result:
		if call.args is None:
			error = call.error or "not valid JSON"
			return _error(
				call,
				ERROR.format(
					error=f"the arguments of {call.name} are not valid: {error}"
				),
			)
		if call.name == self._answer:
			return self._answer_call(call, alone)
		tool = self._tools.get(call.name)
		if tool is None:
			names = ", ".join([*self._tools, *([self._answer] if self._answer else [])])
			return _error(call, NOT_A_TOOL.format(name=call.name, names=names))
		try:
			args = _validate(self._arg_schemas[call.name], call.args)
		except ValidationError as exc:
			return _error(call, ERROR.format(error=f"ValidationError: {exc}"))
		if isinstance(tool, TaskTool):
			return await self._task(agent_id, scope, call, tool, args)
		if isinstance(tool, McpTool):
			return await self._service(agent_id, scope, call, tool, args)
		return await self._inline(call, tool, args)

	async def _inline(self, call: _Call, tool: _Inline, args: dict) -> _Result:
		"""Run an inline tool in an ``execute_tool`` span, as a task's and an
		MCP tool's calls are."""
		t = time.monotonic()
		outcome = "error"
		with _telemetry.tracer.start_as_current_span(
			f"{_telemetry.EXECUTE_TOOL} {tool.name}",
			attributes={
				_telemetry.GEN_AI_OPERATION_NAME: _telemetry.EXECUTE_TOOL,
				_telemetry.GEN_AI_TOOL_NAME: tool.name,
				_telemetry.GEN_AI_TOOL_CALL_ID: call.id,
				_telemetry.TOOL_KIND: "inline",
			},
		) as span:
			try:
				kwargs = {**args, "_call_id": call.id} if tool.with_call_id else args
				if tool.is_async:
					out = tool.fn(**kwargs)
				else:
					# A sync tool runs in a thread, with the agent's context, as
					# LangChain's ToolNode runs one: on the event loop it would
					# hold every agent of the process while it runs.
					out = await asyncio.to_thread(tool.fn, **kwargs)
				if inspect.isawaitable(out):
					out = await out
				outcome = "ok"
			except Interrupt as stop:
				outcome = "interrupted"
				stop.__traceback__ = None  # keeps no frame of the tool's run
				return _Result(interrupt=stop)
			except asyncio.CancelledError:
				outcome = "cancelled"
				raise
			except ValidationError as exc:
				_failed(span, exc)
				return _error(call, ERROR.format(error=f"ValidationError: {exc}"))
			except Exception as exc:  # the model reads what went wrong
				_failed(span, exc)
				return _error(call, ERROR.format(error=repr(exc)))
			finally:
				span.set_attribute(_telemetry.TOOL_OUTCOME, outcome)
				_telemetry.TOOL_DURATION.record(
					time.monotonic() - t,
					{
						_telemetry.GEN_AI_TOOL_NAME: tool.name,
						_telemetry.TOOL_KIND: "inline",
						_telemetry.TOOL_OUTCOME: outcome,
					},
				)
		return _ok(call, out)

	async def _task(
		self, agent_id: str, scope: str, call: _Call, tool: TaskTool, args: dict
	) -> _Result:
		try:
			# Asked before the task is submitted, so a suspend that raises
			# submits nothing.
			suspends = tool.suspends(agent_id, args)
			job = self.runtime.submit(tool, agent_id, call.id, args, scope=scope)
		except ToolSetupError:
			raise  # set up wrong, such as without a session: the agent fails
		except Exception as exc:  # a prepare or suspend that raises: the model reads it
			return _error(call, ERROR.format(error=repr(exc)))
		if not job.done():
			if suspends:
				return _Result(
					wait=(call.id, self.runtime.key(agent_id, call.id, scope))
				)
			with _agents.activity(AgentState.TOOL_WAIT):
				# Withdraws a task not admitted yet if the agent is stopped.
				await _agents.wait((job,))
		if job.cancelled():
			return _error(call, f"{tool.name} failed: its task was cancelled")
		error = job.exception()
		if error is not None:
			return _error(
				call, f"{tool.name} failed: {type(error).__name__}: {error}"[:500]
			)
		try:
			value = job.result()
			if tool.output is not None:
				value = tool.output(value)
		except Exception as exc:
			return _error(call, ERROR.format(error=repr(exc)))
		return _ok(call, value)

	async def _service(
		self, agent_id: str, scope: str, call: _Call, tool: McpTool, args: dict
	) -> _Result:
		with _agents.activity(AgentState.TOOL_CALL):
			try:
				value = await self.runtime.call_service(
					tool, agent_id, call.id, args, scope=scope
				)
			except ToolSetupError:
				raise  # set up wrong: no service client, or one not started
			except Exception as exc:  # the model reads the failure
				return _error(
					call, f"{tool.name} failed: {type(exc).__name__}: {exc}"[:500]
				)
		return _ok(call, value)

	def _answer_call(self, call: _Call, alone: bool) -> _Result:
		if not alone:
			return _error(
				call,
				ERROR.format(
					error=f"{self._answer} gives the final answer: call it on its "
					"own, once the other tools' results are in"
				),
			)
		try:
			answer = self.response_format.model_validate(call.args)
		except ValidationError as exc:
			return _error(call, ERROR.format(error=f"ValidationError: {exc}"))
		return _Result(_result(call, ANSWERED), answer=answer)

	# --- state -----------------------------------------------------------------------

	def _save(self, agent_id: str, messages: list[dict], calls: int) -> None:
		self.store.put(self.key(agent_id), {"messages": messages, "model_calls": calls})
		self.store_puts += 1
		self.suspensions += 1
		self._saved.add(agent_id)
		# An agent the host cancels or fails while it waits never comes back
		# here: its state goes when the host ends it.
		_agents.at_agent_end(agent_id, partial(self._abandoned, agent_id))

	def _abandoned(self, agent_id: str) -> None:
		if agent_id in self._saved:
			self._end(agent_id)

	def _end(self, agent_id: str) -> None:
		"""The agent makes no more calls: forget its state, and tell the fleet."""
		if agent_id in self._saved:
			self._saved.discard(agent_id)
			try:
				self.store.delete(self.key(agent_id))
				self.store_deletes += 1
			except Exception as exc:
				# The agent's own outcome stands; only its state stays behind.
				self.delete_failures += 1
				log.log(
					logging.WARNING if self.delete_failures == 1 else logging.DEBUG,
					"could not delete agent %s's state (%s: %s); it stays in the store",
					agent_id,
					type(exc).__name__,
					exc,
				)
		if self._fleet is not None:
			self._fleet.agent_done(agent_id)


# --- messages ------------------------------------------------------------------------

_ROLES = {"human": "user", "ai": "assistant"}


def _messages(value: object) -> list[dict]:
	"""An agent's input as a new list of OpenAI-format messages."""
	if isinstance(value, dict) and "messages" in value:
		value = value["messages"]
	if (
		isinstance(value, (str, dict))
		or _role_and_content(value)
		or not isinstance(value, Iterable)
		or _is_langchain(value, "langchain_core.messages", "BaseMessage")
	):
		value = [value]
	out: list[dict] = []
	for m in value:
		if isinstance(m, str):
			out.append({"role": "user", "content": m})
		elif isinstance(m, dict) and "role" in m:
			out.append(m)
		elif isinstance(m, tuple) and len(m) == 2:
			out.append({"role": _ROLES.get(m[0], m[0]), "content": m[1]})
		elif _is_langchain(m, "langchain_core.messages", "BaseMessage"):
			from flowgentic.langchain import to_openai_messages

			out.extend(to_openai_messages([m]))
		else:
			raise TypeError(f"not a message: {m!r}")
	return out


def _role_and_content(value: object) -> bool:
	"""Whether ``value`` is one message as a ``(role, content)`` tuple, as
	LangGraph reads one."""
	return isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str)


def _failed(span: Any, exc: BaseException) -> None:  # noqa: ANN401  # a span
	"""Mark an inline tool's span failed, as a task's is: its error's type,
	status ERROR and the exception."""
	span.set_attribute(_telemetry.ERROR_TYPE, type(exc).__qualname__)
	span.record_exception(exc)
	span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"[:200]))


def _calls_of(reply: Reply, message: dict) -> list[_Call]:
	"""A reply's tool calls, those whose arguments did not parse included."""
	raw = {c["id"]: c for c in message.get("tool_calls") or ()}
	calls = [_Call(c.id, c.name, c.args, None, raw[c.id]) for c in reply.tool_calls]
	calls.extend(
		_Call(c.id, c.name, None, f"{c.error}: {c.arguments}", raw[c.id])
		for c in reply.invalid_tool_calls
	)
	return calls


def _pending(messages: list[dict]) -> tuple[int, list[_Call], list[_Call]]:
	"""The last reply's index, its calls, and those with no result yet, read
	from the messages, as when an agent resumes."""
	for i in range(len(messages) - 1, -1, -1):
		m = messages[i]
		if m.get("role") != "assistant":
			continue
		made = []
		for c in m.get("tool_calls") or ():
			function = c.get("function") or {}
			try:
				args = json.loads(function.get("arguments") or "{}")
			except ValueError as exc:
				args, error = None, f"not valid JSON: {exc}"
			else:
				error = None
				if not isinstance(args, dict):
					args, error = None, "not a JSON object"
			made.append(_Call(c["id"], function.get("name") or "", args, error, c))
		if not made:
			return i, [], []
		after = messages[i + 1 :]
		if any(x.get("role") != "tool" for x in after):
			# The conversation moved past these calls, as when a user's message
			# follows them in an input history: none is pending.
			return i, made, []
		answered = {x.get("tool_call_id") for x in after}
		return i, made, [c for c in made if c.id not in answered]
	return -1, [], []


def _order(messages: list[dict], i: int, made: list[_Call]) -> None:
	"""Put the results after reply ``i`` in the order of its calls: a resumed
	call's result arrives after the others'."""
	order = {c.id: n for n, c in enumerate(made)}
	last = len(order)
	messages[i + 1 :] = sorted(
		messages[i + 1 :], key=lambda m: order.get(m.get("tool_call_id"), last)
	)


def _result(call: _Call, content: str) -> dict:
	return {"role": "tool", "tool_call_id": call.id, "content": content}


def _error(call: _Call, content: str) -> _Result:
	return _Result(_result(call, content), error=True)


def _ok(call: _Call, value: object) -> _Result:
	text = (
		value
		if isinstance(value, str)
		else json.dumps(value, default=str, ensure_ascii=False)
	)
	return _Result(_result(call, text))


# --- tools ---------------------------------------------------------------------------


def _is_langchain(obj: object, module: str, name: str) -> bool:
	"""Whether ``obj`` is an instance of LangChain's ``module.name``, without
	importing LangChain: if it is not loaded, nothing is one."""
	loaded = sys.modules.get(module)
	cls = getattr(loaded, name, None)
	return cls is not None and isinstance(obj, cls)


def _inline(tool: object) -> _Inline:
	"""An inline tool: a LangChain tool, through the adapter, or a callable,
	whose ``name``, ``description`` and ``args_schema`` attributes are used if
	it has them, and otherwise its name, docstring and signature."""
	if _is_langchain(tool, "langchain_core.tools", "BaseTool"):
		from flowgentic.langchain import inline_tool

		tool = inline_tool(tool)
	if not callable(tool):
		raise TypeError(f"not a tool: {tool!r}")
	name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
	if not isinstance(name, str):
		raise TypeError(f"not a tool: {tool!r}: give it a name")
	description = getattr(tool, "description", None)
	if not isinstance(description, str):
		description = inspect.getdoc(tool) or name
	schema = getattr(tool, "args_schema", None)
	if schema is None:
		schema = _signature_schema(name, tool)
	with_call_id = getattr(tool, "takes_call_id", False) is True
	is_async = inspect.iscoroutinefunction(tool) or inspect.iscoroutinefunction(
		getattr(tool, "__call__", None)
	)
	return _Inline(name, description, schema, tool, with_call_id, is_async)


def _signature_schema(name: str, fn: Callable[..., Any]) -> type[BaseModel]:
	"""A pydantic model of ``fn``'s parameters, from its signature and type
	hints; ``*args`` and ``**kwargs`` are left out."""
	target = fn if inspect.isfunction(fn) or inspect.ismethod(fn) else fn.__call__
	try:
		hints = typing.get_type_hints(target)
	except Exception:  # unresolvable annotations: no types
		hints = {}
	fields: dict[str, Any] = {}
	for p in inspect.signature(fn).parameters.values():
		if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
			continue
		annotation = hints.get(p.name, Any)
		fields[p.name] = (annotation, ... if p.default is p.empty else p.default)
	return create_model(name, **fields)


def _tool_schema(name: str, description: str, schema: object) -> dict:
	"""A tool as the model sees it: OpenAI's function tool, its parameters
	from a pydantic model (titles dropped, as LangChain drops them) or a JSON
	schema as it is."""
	if isinstance(schema, type) and issubclass(schema, BaseModel):
		parameters = schema.model_json_schema()
		parameters.pop("title", None)
		for prop in (parameters.get("properties") or {}).values():
			if isinstance(prop, dict):
				prop.pop("title", None)
	elif isinstance(schema, dict):
		parameters = schema
	else:
		parameters = {"type": "object", "properties": {}}
	return {
		"type": "function",
		"function": {
			"name": name,
			"description": description,
			"parameters": parameters,
		},
	}


def _validate(schema: object, args: dict) -> dict:
	"""``args`` checked against a pydantic schema, as a LangChain tool checks
	them: the arguments given, by their fields' names (one given under its
	alias included), as the schema made them. A schema that is not a pydantic
	model leaves them as they are."""
	if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
		return dict(args)
	model = schema.model_validate(args)
	given = model.model_fields_set
	return {k: getattr(model, k) for k in type(model).model_fields if k in given}
