"""The agent loop on ``langchain-core``: LangChain's messages, a fleet or a
LangChain chat model, and LangChain tools inline. The loop's only backend for
now; see ``flowgentic.loop._seams``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

try:
	from langchain_core.messages import (
		AIMessage,
		BaseMessage,
		HumanMessage,
		SystemMessage,
		ToolMessage,
		convert_to_messages,
	)
	from langchain_core.tools import (
		BaseTool,
		StructuredTool,
		create_schema_from_function,
	)
except ModuleNotFoundError as exc:
	if exc.name != "langchain_core":
		raise
	raise ModuleNotFoundError(
		"flowgentic.loop needs langchain-core: install flowgentic[langchain-core]",
		name=exc.name,
	) from exc

from flowgentic.fleet import Fleet, FleetChatModel
from flowgentic.loop._seams import (
	Backend,
	Bindable,
	Call,
	InlineTool,
	ModelCall,
	Spec,
	text,
)


class LangChain(Backend):
	"""``model``: a ``Fleet``, a ``FleetChatModel`` (run as its fleet) or a
	LangChain chat model."""

	def __init__(self, model: object) -> None:
		if isinstance(model, FleetChatModel):
			model = model.fleet
		self.model = model

	# --- messages ---------------------------------------------------------------

	def to_list(self, value: object) -> list:
		if isinstance(value, dict):
			value = value["messages"]
		if isinstance(value, (str, BaseMessage)):
			value = [value]
		return list(convert_to_messages(value))

	def system(self, text: str) -> SystemMessage:
		return SystemMessage(text)

	def human(self, text: str) -> HumanMessage:
		return HumanMessage(text)

	def result(self, call: Call, content: str, error: bool) -> ToolMessage:
		return ToolMessage(
			content,
			tool_call_id=call.id,
			name=call.name,
			status="error" if error else "success",
		)

	def calls(self, message: object) -> list[Call] | None:
		if not isinstance(message, AIMessage):
			return None
		calls = [
			Call(c["id"], c["name"], c["args"], None, c) for c in message.tool_calls
		]
		calls.extend(
			Call(c["id"], c.get("name") or "", None, c.get("error") or "", c)
			for c in message.invalid_tool_calls
			if c.get("id")
		)
		return calls

	def answers(self, message: object) -> str | None:
		return message.tool_call_id if isinstance(message, ToolMessage) else None

	def text(self, message: object) -> str:
		return message.text

	# --- tools ------------------------------------------------------------------

	def schema(self, name: str, fn: Callable[..., Any]) -> object:
		return create_schema_from_function(name, fn)

	def inline(self, tool: object) -> InlineTool | None:
		return _Tool(tool) if isinstance(tool, BaseTool) else None

	# --- the model ----------------------------------------------------------------

	def bind(self, tools: list[Bindable]) -> ModelCall:
		bound: list[Any] = []
		for tool in tools:
			if isinstance(tool, _Tool):
				bound.append(tool.tool)
			elif tool.answer:
				bound.append(tool.schema)  # a pydantic model, as LangChain binds one
			else:
				# Only its schema reaches the model; the loop runs the call.
				bound.append(
					StructuredTool(
						name=tool.name,
						description=tool.description,
						args_schema=tool.schema,
					)
				)
		model = self.model.bind_tools(bound) if bound else self.model
		if isinstance(self.model, Fleet):
			return _FleetCall(model, self.model)
		return _ChatCall(model)


class _Tool(InlineTool):
	def __init__(self, tool: BaseTool) -> None:
		self.tool = tool
		self.name = tool.name

	async def run(self, call: Call) -> tuple[Any, bool]:
		out = await self.tool.ainvoke(
			{"name": call.name, "args": call.args, "id": call.id, "type": "tool_call"}
		)
		if not isinstance(out, ToolMessage):
			out = ToolMessage(text(out), tool_call_id=call.id, name=call.name)
		return out, out.status == "error"


class _FleetCall(ModelCall):
	counts_states = True  # the fleet counts queued and in-flight calls

	def __init__(self, view: Fleet, fleet: Fleet) -> None:
		self.view = view
		self.fleet = fleet

	async def __call__(self, agent_id: str, messages: list) -> AIMessage:
		return await self.view.ainvoke(agent_id, messages)

	def agent_done(self, agent_id: str) -> None:
		self.fleet.agent_done(agent_id)


class _ChatCall(ModelCall):
	def __init__(self, model: Any) -> None:  # noqa: ANN401  # a Runnable
		self.model = model

	async def __call__(self, agent_id: str, messages: list) -> AIMessage:
		return await self.model.ainvoke(messages)
