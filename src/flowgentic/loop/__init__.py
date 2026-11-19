"""Flowgentic's own agent loop: a runner for the agent host, without LangGraph.

``LoopRunner(model, tools)`` runs tool-calling agents in a
``flowgentic.host.AgentHost``. The model replies; the tool calls of its reply
run, all at once; their results go back to the model; and so on until the
model answers. It is the agent that ``GraphRunner`` runs with a ReAct graph,
written as a ``while`` loop. ``GraphRunner`` stays, for graphs and for what
LangGraph adds: streaming, middleware, human-in-the-loop resumes.

The loop reads and makes messages, calls the model and runs inline tools
through a backend (``flowgentic.loop._seams``). The one it ships is on
``langchain-core``: LangChain's messages, the fleet or a LangChain chat model,
and LangChain tools. The rest of the loop depends on none of them.

- **The model** is a ``flowgentic.fleet.Fleet``, which routes and admits each
  call by agent, or any LangChain chat model. Its tools are bound once.
- **Tools** are ``flowgentic.tools`` declarations, which run on a
  ``ToolRuntime`` (task tools as RHAPSODY tasks, MCP tools through the tool
  service), and LangChain tools, which run inline. The calls of one reply run
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
  failed task or MCP call: each becomes a tool result marked as an error, which
  the model reads. A runtime set up wrong, without the session or the service
  client a tool needs, fails the agent instead.
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
  holds. It must keep each tool call with its result, as
  ``langchain_core.messages.trim_messages`` can.
- **Interrupts.** An inline tool that raises ``Interrupt(value)`` stops its
  agent: the host ends it as interrupted, with an ``Interruption`` that holds
  the value, the call and the history. The store keeps nothing of it.
- **Accounting and telemetry.** The agent's states are counted as the host's
  other parts count them: model calls through the fleet, waits on tasks, calls
  to the tool service, inline tools as working. Task and MCP calls are
  ``execute_tool`` spans under the agent's ``invoke_agent`` span, and each
  suspension is the host's ``flowgentic.agent.suspend`` event.

Messages are in the backend's format: LangChain messages, with the fleet or a
LangChain chat model. An agent's input is then a string, a message, a list of
messages (or of anything ``langchain_core.messages.convert_to_messages``
takes) or a dict with ``messages``, as a LangGraph graph's input.
``Agent.config`` is not read. A finished agent's output is a ``LoopOutput``.
The runner never changes the caller's list of messages.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

from pydantic import BaseModel, ValidationError

from flowgentic import _agents
from flowgentic._agents import AgentState
from flowgentic.host import Agent, Finished, Interrupted, Step, Suspended
from flowgentic.loop._seams import Backend, Bindable, Call, InlineTool, Spec, text
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

Trim = Callable[[list[Any]], Sequence[Any] | Awaitable[Sequence[Any]]]


@dataclass(frozen=True, slots=True)
class LoopOutput:
	"""A finished agent's output: its history, the validated answer when the
	runner has a ``response_format``, and its model calls."""

	messages: list[Any]
	structured_response: Any = None
	model_calls: int = 0


@dataclass(frozen=True, slots=True)
class Interruption:
	"""Why an agent stopped: the value its tool raised ``Interrupt`` with, the
	tool call, and the agent's history then, in which that call has no result."""

	value: Any
	tool_call: dict
	messages: list[Any]
	model_calls: int


class Interrupt(Exception):  # noqa: N818  # a signal, as LangGraph's interrupt
	"""Raised by an inline tool to stop its agent; the host ends the agent as
	interrupted, with an ``Interruption`` that carries ``value``."""

	def __init__(self, value: object = None) -> None:
		super().__init__(value)
		self.value = value


class ModelCallLimitExceeded(RuntimeError):  # noqa: N818
	"""An agent needed more model calls than its runner's ``max_model_calls``."""


class _Result:
	"""What one tool call gave: a result for the model (an error or not), a task
	to wait on, an interrupt, or a structured answer."""

	__slots__ = ("message", "error", "wait", "interrupt", "answer")

	def __init__(
		self,
		message: object = None,
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

	``model``: a ``Fleet``, a ``FleetChatModel`` or a LangChain chat model.
	``tools``: ``task_tool`` and ``mcp_tool`` declarations, which need
	``runtime``, and LangChain tools. ``store``: where suspended agents' state
	goes, a ``MemoryStore`` by default; each agent's key is ``key_prefix`` and
	its id. ``system_prompt`` goes first in every model call and is never saved
	or trimmed. ``response_format``, ``max_model_calls`` and ``trim``: see the
	module's docstring.
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
		key_prefix: str = "loop/",
	) -> None:
		if max_model_calls < 1:
			raise ValueError("max_model_calls must be at least 1")
		if isinstance(model, Backend):
			backend = model
		else:
			from flowgentic.loop._langchain import LangChain

			backend = LangChain(model)
		self._backend = backend
		self.runtime = runtime
		self.store = store if store is not None else MemoryStore()
		self.system_prompt = system_prompt
		self.response_format = response_format
		self.max_model_calls = max_model_calls
		self.trim = trim
		self.key_prefix = key_prefix
		self._system = None if system_prompt is None else backend.system(system_prompt)

		self._tools: dict[str, TaskTool | McpTool | InlineTool] = {}
		self._schemas: dict[str, Any] = {}
		bound: list[Bindable] = []
		for tool in tools:
			if isinstance(tool, (TaskTool, McpTool)):
				if runtime is None:
					raise ValueError(
						f"{tool.name} is a task or MCP tool: the runner needs a "
						"ToolRuntime (runtime=...)"
					)
				schema = tool.args_schema
				if schema is None and isinstance(tool, TaskTool):
					schema = backend.schema(tool.name, tool.fn)
				self._schemas[tool.name] = schema
				bound.append(Spec(tool.name, tool.description, schema))
				runs: TaskTool | McpTool | InlineTool = tool
			else:
				runs = backend.inline(tool)
				if runs is None:
					raise TypeError(f"not a tool: {tool!r}")
				bound.append(runs)
			if runs.name in self._tools:
				raise ValueError(f"two tools are named {runs.name!r}")
			self._tools[runs.name] = runs
		self._answer: str | None = None
		if response_format is not None:
			self._answer = response_format.__name__
			if self._answer in self._tools:
				raise ValueError(
					f"a tool is named {self._answer!r}, as the response format is"
				)
			description = (response_format.__doc__ or "").strip() or self._answer
			bound.append(Spec(self._answer, description, response_format, answer=True))
		self._model = backend.bind(bound)

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
		return await self._run(agent.id, self._backend.to_list(agent.input), 0)

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

	async def _run(self, agent_id: str, messages: list, calls: int) -> Step:
		try:
			step = await self._loop(agent_id, messages, calls)
		except BaseException:
			self._end(agent_id)
			raise
		if not isinstance(step, Suspended):
			self._end(agent_id)
		return step

	async def _loop(self, agent_id: str, messages: list, calls: int) -> Step:
		backend = self._backend
		while True:
			i, made, pending = self._pending(messages)
			if pending:
				step = await self._tools_turn(
					agent_id, messages, calls, i, made, pending
				)
				if step is not None:
					return step
				continue
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
			messages.append(reply)
			if backend.calls(reply):
				continue
			if self._answer is None:
				return Finished(LoopOutput(messages, None, calls))
			answer = self._parse(reply)
			if answer is not None:
				return Finished(LoopOutput(messages, answer, calls))
			messages.append(
				backend.human(f"Answer by calling the {self._answer} tool, on its own.")
			)

	def _pending(self, messages: list) -> tuple[int, list[Call], list[Call]]:
		"""The last reply's index, its calls, and those with no result yet."""
		backend = self._backend
		for i in range(len(messages) - 1, -1, -1):
			made = backend.calls(messages[i])
			if made is None:
				continue
			if not made:
				return i, made, []
			answered = {backend.answers(m) for m in messages[i + 1 :]}
			return i, made, [c for c in made if c.id not in answered]
		return -1, [], []

	async def _trimmed(self, messages: list) -> list:
		out = self.trim(messages)
		if inspect.isawaitable(out):
			out = await out
		return list(out)

	async def _ask(self, agent_id: str, messages: list) -> object:
		sent = messages if self._system is None else [self._system, *messages]
		if self._model.counts_states:
			return await self._model(agent_id, sent)
		with _agents.activity(AgentState.MODEL_REQUEST):
			return await self._model(agent_id, sent)

	def _parse(self, reply: object) -> BaseModel | None:
		try:
			return self.response_format.model_validate_json(self._backend.text(reply))
		except ValidationError:
			return None

	async def _tools_turn(
		self,
		agent_id: str,
		messages: list,
		calls: int,
		i: int,
		made: list[Call],
		pending: list[Call],
	) -> Step | None:
		scope = str(calls)  # the turn: the same when the agent resumes
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
		self._order(messages, i, made)
		if stops:
			self.interrupts += 1
			return Interrupted(tuple(stops))
		if waits:
			self._save(agent_id, messages, calls)
			return Suspended(tuple(waits))
		if answer is not None:
			return Finished(LoopOutput(messages, answer, calls))
		return None

	def _order(self, messages: list, i: int, made: list[Call]) -> None:
		"""Put the results after reply ``i`` in the order of its calls: a resumed
		call's result arrives after the others'."""
		order = {c.id: n for n, c in enumerate(made)}
		last = len(order)
		answers = self._backend.answers
		messages[i + 1 :] = sorted(
			messages[i + 1 :], key=lambda m: order.get(answers(m), last)
		)

	# --- one tool call ---------------------------------------------------------------

	def _error(self, call: Call, content: str) -> _Result:
		return _Result(self._backend.result(call, content, True), error=True)

	def _ok(self, call: Call, value: object) -> _Result:
		return _Result(self._backend.result(call, text(value), False))

	async def _call(
		self, agent_id: str, scope: str, call: Call, alone: bool
	) -> _Result:
		if call.args is None:
			error = call.error or "not valid JSON"
			return self._error(
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
			return self._error(call, NOT_A_TOOL.format(name=call.name, names=names))
		if isinstance(tool, TaskTool):
			return await self._task(agent_id, scope, call, tool)
		if isinstance(tool, McpTool):
			return await self._service(agent_id, scope, call, tool)
		return await self._inline(call, tool)

	async def _inline(self, call: Call, tool: InlineTool) -> _Result:
		try:
			message, error = await tool.run(call)
		except Interrupt as stop:
			stop.__traceback__ = None  # keeps no frame of the tool's run
			return _Result(interrupt=stop)
		except ValidationError as exc:
			return self._error(call, ERROR.format(error=f"ValidationError: {exc}"))
		except Exception as exc:  # the model reads what went wrong
			return self._error(call, ERROR.format(error=repr(exc)))
		return _Result(message, error=error)

	async def _task(
		self, agent_id: str, scope: str, call: Call, tool: TaskTool
	) -> _Result:
		try:
			args = _validate(self._schemas[tool.name], call.args)
		except ValidationError as exc:
			return self._error(call, ERROR.format(error=f"ValidationError: {exc}"))
		# A runtime without a session raises RuntimeError here: the agent fails.
		job = self.runtime.submit(tool, agent_id, call.id, args, scope=scope)
		if not job.done():
			if tool.suspends(agent_id, args):
				return _Result(
					wait=(call.id, self.runtime.key(agent_id, call.id, scope))
				)
			with _agents.activity(AgentState.TOOL_WAIT):
				# Withdraws a task not admitted yet if the agent is stopped.
				await _agents.wait((job,))
		if job.cancelled():
			return self._error(call, f"{tool.name} failed: its task was cancelled")
		error = job.exception()
		if error is not None:
			return self._error(
				call, f"{tool.name} failed: {type(error).__name__}: {error}"[:500]
			)
		try:
			value = job.result()
			if tool.output is not None:
				value = tool.output(value)
		except Exception as exc:
			return self._error(call, ERROR.format(error=repr(exc)))
		return self._ok(call, value)

	async def _service(
		self, agent_id: str, scope: str, call: Call, tool: McpTool
	) -> _Result:
		try:
			args = _validate(self._schemas[tool.name], call.args)
		except ValidationError as exc:
			return self._error(call, ERROR.format(error=f"ValidationError: {exc}"))
		with _agents.activity(AgentState.TOOL_CALL):
			try:
				value = await self.runtime.call_service(
					tool, agent_id, call.id, args, scope=scope
				)
			except RuntimeError:
				raise  # set up wrong: no service client, or one not started
			except Exception as exc:  # the model reads the failure
				return self._error(
					call, f"{tool.name} failed: {type(exc).__name__}: {exc}"[:500]
				)
		return self._ok(call, value)

	def _answer_call(self, call: Call, alone: bool) -> _Result:
		if not alone:
			return self._error(
				call,
				ERROR.format(
					error=f"{self._answer} gives the final answer: call it on its "
					"own, once the other tools' results are in"
				),
			)
		try:
			answer = self.response_format.model_validate(call.args)
		except ValidationError as exc:
			return self._error(call, ERROR.format(error=f"ValidationError: {exc}"))
		return _Result(self._backend.result(call, ANSWERED, False), answer=answer)

	# --- state -----------------------------------------------------------------------

	def _save(self, agent_id: str, messages: list, calls: int) -> None:
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
		"""The agent makes no more calls: forget its state, and tell the model."""
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
		self._model.agent_done(agent_id)


def _validate(schema: object, args: dict) -> dict:
	"""``args`` checked against a pydantic schema, as a LangChain tool checks
	them: the arguments given, as the schema made them. A schema that is not a
	pydantic model leaves them as they are."""
	if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
		return dict(args)
	model = schema.model_validate(args)
	return {k: getattr(model, k) for k in type(model).model_fields if k in args}
