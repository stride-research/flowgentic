"""Flowgentic's tools as LangChain tools, for LangGraph agents.

``bind_tools(runtime, tools)`` turns ``flowgentic.tools`` declarations into
LangChain tools that run on ``runtime``, and passes LangChain tools through
unchanged: those run inline.

A task tool that suspends submits its task, then calls LangGraph's
``interrupt`` with the task's key. The run stops and its checkpoint is saved;
the agent host awaits the task and resumes the graph. LangGraph then runs the
tool again: the submission finds the task already there, ``interrupt`` returns,
and the tool returns the task's result. The user's function runs once, in a
RHAPSODY worker.

A failed, refused or lost task or service call reaches the model as the
tool's error, so the agent can read it and decide what to do.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Annotated, Any

from langchain_core.runnables.config import ensure_config
from langchain_core.tools import (
	BaseTool,
	InjectedToolCallId,
	StructuredTool,
	ToolException,
	create_schema_from_function,
)
from langgraph.errors import GraphInterrupt
from langgraph.types import interrupt
from pydantic import create_model

from flowgentic import _agents
from flowgentic._agents import AgentState
from flowgentic.langgraph.runner import PENDING_KEY, record_interrupt
from flowgentic.tools import McpTool, TaskTool, ToolRuntime

__all__ = ["bind_tools"]

_CALL_ID = "tool_call_id"


def bind_tools(runtime: ToolRuntime, tools: Iterable[Any]) -> list[BaseTool]:
	"""LangChain tools for ``tools``: task and MCP tools run on ``runtime``;
	LangChain tools are returned as they are."""
	out: list[BaseTool] = []
	for tool in tools:
		if isinstance(tool, TaskTool):
			out.append(_task_tool(runtime, tool))
		elif isinstance(tool, McpTool):
			out.append(_mcp_tool(runtime, tool))
		elif isinstance(tool, BaseTool):
			out.append(tool)
		else:
			raise TypeError(f"not a tool: {tool!r}")
	return out


def _agent_id() -> str:
	agent = (ensure_config().get("configurable") or {}).get("thread_id")
	agent = agent or _agents.current_agent()
	if not agent:
		raise ToolException("a task or MCP tool needs the agent's thread_id")
	return str(agent)


def _with_call_id(schema: Any) -> Any:
	"""``schema`` with the tool call's id injected; the model never sees it."""
	return create_model(
		schema.__name__,
		__base__=schema,
		**{_CALL_ID: (Annotated[str, InjectedToolCallId], ...)},
	)


def _output(value: Any) -> str:
	return value if isinstance(value, str) else json.dumps(value, default=str)


def _task_tool(runtime: ToolRuntime, tool: TaskTool) -> BaseTool:
	async def run(**kwargs: Any) -> str:
		call_id = kwargs.pop(_CALL_ID)
		agent = _agent_id()
		job = runtime.submit(tool, agent, call_id, kwargs)
		if tool.suspends(agent, kwargs):
			try:
				interrupt({PENDING_KEY: runtime.key(agent, call_id)})
			except GraphInterrupt as exc:
				record_interrupt(exc)  # freed when the run stops
				raise
		with _agents.activity(AgentState.TOOL_WAIT):
			try:
				value = await job
			except Exception as exc:  # the model reads the failure
				raise ToolException(
					f"{tool.name} failed: {type(exc).__name__}: {exc}"[:500]
				) from exc
		if tool.output is not None:
			value = tool.output(value)
		return _output(value)

	schema = tool.args_schema or create_schema_from_function(tool.name, tool.fn)
	return StructuredTool(
		name=tool.name,
		description=tool.description,
		args_schema=_with_call_id(schema),
		coroutine=run,
		handle_tool_error=True,
	)


def _mcp_tool(runtime: ToolRuntime, tool: McpTool) -> BaseTool:
	async def run(**kwargs: Any) -> str:
		call_id = kwargs.pop(_CALL_ID)
		agent = _agent_id()
		with _agents.activity(AgentState.TOOL_CALL):
			try:
				value = await runtime.call_service(tool, agent, call_id, kwargs)
			except Exception as exc:  # the model reads the failure
				raise ToolException(
					f"{tool.name} failed: {type(exc).__name__}: {exc}"[:500]
				) from exc
		return _output(value)

	return StructuredTool(
		name=tool.name,
		description=tool.description,
		args_schema=_with_call_id(tool.args_schema),
		coroutine=run,
		handle_tool_error=True,
	)
