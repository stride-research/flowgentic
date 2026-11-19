"""The agent loop's seams: how it reads and makes messages, calls the model and
handles tools, so that the loop itself depends on no message format or model
client.

A ``Backend`` implements all three for one stack. ``flowgentic.loop._langchain``
is the only one Flowgentic ships: LangChain's messages, its chat models or the
fleet, and its tools. Another, on OpenAI-format dicts and an OpenAI-compatible
client, would implement the same methods; the loop would not change. A runner
given a ``Backend`` as its model uses it as it is.

- **Messages** are opaque to the loop. It asks the backend to turn an agent's
  input into a list, to make a system, a human and a tool-result message, to
  read a reply's tool calls, the call a tool result answers, and a reply's
  text.
- **The model call**: ``bind`` gets the tools in the order they were declared
  and returns a ``ModelCall`` that sends an agent's messages and returns the
  reply.
- **Tools**: ``schema`` makes a task tool's argument schema from its function;
  ``inline`` turns one of the stack's own tools into an ``InlineTool``, which
  the loop runs in the agent's process.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Call:
	"""One tool call of a reply. ``args`` is ``None`` when the model's
	arguments did not parse, and ``error`` says why. ``raw`` is the call as the
	backend's format holds it."""

	id: str
	name: str
	args: dict | None
	error: str | None
	raw: dict


@dataclass(frozen=True, slots=True)
class Spec:
	"""A tool as the model sees it: its name, its description and its argument
	schema, a pydantic model or a JSON schema. ``answer`` marks the tool of the
	runner's structured answer, whose schema is the answer's model."""

	name: str
	description: str
	schema: Any
	answer: bool = False


class InlineTool(ABC):
	"""A tool the loop runs in the agent's process."""

	name: str

	@abstractmethod
	async def run(self, call: Call) -> tuple[Any, bool]:
		"""Run ``call``; return the tool-result message, and whether it reports
		an error. Raise to have the loop report the error, and let
		``flowgentic.loop.Interrupt`` through."""


class ModelCall(ABC):
	"""The model, with its tools bound."""

	counts_states: bool = False
	"""Whether the call counts the agent's model states itself, as the fleet
    does; otherwise the loop counts the call as a model request."""

	@abstractmethod
	async def __call__(self, agent_id: str, messages: list) -> object:
		"""The model's reply to ``messages``, for ``agent_id``."""

	def agent_done(self, agent_id: str) -> None:  # noqa: B027  # optional
		"""The agent makes no more calls."""


Bindable = Spec | InlineTool


class Backend(ABC):
	"""Messages, the model call and tools for one stack; see the module's
	docstring."""

	@abstractmethod
	def to_list(self, value: object) -> list:
		"""An agent's input as a new list of messages."""

	@abstractmethod
	def system(self, text: str) -> object:
		"""A system message."""

	@abstractmethod
	def human(self, text: str) -> object:
		"""A message from the user."""

	@abstractmethod
	def result(self, call: Call, content: str, error: bool) -> object:
		"""The result of ``call`` for the model; ``error`` if it failed."""

	@abstractmethod
	def calls(self, message: object) -> list[Call] | None:
		"""A reply's tool calls, those whose arguments did not parse included;
		``None`` if ``message`` is not a model's reply."""

	@abstractmethod
	def answers(self, message: object) -> str | None:
		"""The id of the call that a tool-result message answers, or ``None``."""

	@abstractmethod
	def text(self, message: object) -> str:
		"""A reply's text."""

	@abstractmethod
	def schema(self, name: str, fn: Callable[..., Any]) -> object:
		"""A task tool's argument schema, from its function."""

	@abstractmethod
	def inline(self, tool: object) -> InlineTool | None:
		"""``tool`` as an inline tool, or ``None`` if it is not one of the
		stack's tools."""

	@abstractmethod
	def bind(self, tools: list[Bindable]) -> ModelCall:
		"""The model call, with ``tools`` bound, in their order."""


def text(value: object) -> str:
	"""A tool's value as the text the model reads."""
	return value if isinstance(value, str) else json.dumps(value, default=str)
