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
tool's error, so the agent can read it and decide what to do. A runtime set
up wrong, without the session or the started service client a tool needs,
raises ``RuntimeError`` and fails the agent.
"""

from __future__ import annotations

import asyncio
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
	LangChain tools are returned as they are. ``ValueError`` if ``runtime``
	cannot run a task tool's tasks (``ToolRuntime.check``)."""
	out: list[BaseTool] = []
	for tool in tools:
		if isinstance(tool, TaskTool):
			runtime.check(tool)  # a runtime set up wrong fails here, not in an agent
			out.append(_task_tool(runtime, tool))
		elif isinstance(tool, McpTool):
			out.append(_mcp_tool(runtime, tool))
		elif isinstance(tool, BaseTool):
			out.append(tool)
		else:
			raise TypeError(f"not a tool: {tool!r}")
	return out


def _agent_and_scope() -> tuple[str, str]:
	"""The agent's id, and the LangGraph task that runs the tool call.

	A model server may number its tool calls per message, so a later turn can
	reuse an earlier turn's call id. The task's id tells the two apart: each
	step's tasks have their own, and a task that runs again on resume has the
	same. It is the last part of the run's ``checkpoint_ns``, whose id is
	derived from the namespaces above it, so a tool in a subgraph has one too.
	Outside a LangGraph task there is none, and the call id alone keys the
	call."""
	configurable = ensure_config().get("configurable") or {}
	agent = configurable.get("thread_id") or _agents.current_agent()
	if not agent:
		raise ToolException("a task or MCP tool needs the agent's thread_id")
	ns = configurable.get("checkpoint_ns") or ""
	_, sep, task = ns.rpartition("|")[2].partition(":")
	return str(agent), task if sep else ""


def _with_call_id(schema: Any) -> Any:
	"""``schema`` with the tool call's id injected; the model never sees it."""
	return create_model(
		schema.__name__,
		__base__=schema,
		**{_CALL_ID: (Annotated[str, InjectedToolCallId], ...)},
	)


def _output(value: Any) -> str:
	return value if isinstance(value, str) else json.dumps(value, default=str)


async def _settled(job: asyncio.Future) -> None:
	"""Wait for a task to end without raising its exception here.

	The runtime keeps an ended task, and RHAPSODY's session its failure, so the
	task's exception lives on. Raised here, it would keep this tool's frames in
	its traceback, and on CPython 3.12 those of the LangGraph run that called
	the tool, with the agent's whole state.

	Cancelling the wait leaves a started task running. The task belongs to
	every call with its key, such as the same call made again once its agent
	runs again, and RHAPSODY does not stop a task that has started; so the task
	keeps its place in its ``TaskOrder`` until it ends. A task its order has
	not admitted yet is withdrawn once no call waits for it."""
	await _agents.wait((job,))


def _task_tool(runtime: ToolRuntime, tool: TaskTool) -> BaseTool:
	async def run(**kwargs: Any) -> str:
		call_id = kwargs.pop(_CALL_ID)
		agent, scope = _agent_and_scope()
		job = runtime.submit(tool, agent, call_id, kwargs, scope=scope)
		if tool.suspends(agent, kwargs):
			try:
				interrupt({PENDING_KEY: runtime.key(agent, call_id, scope)})
			except GraphInterrupt as exc:
				record_interrupt(exc)  # freed when the run stops
				raise
		with _agents.activity(AgentState.TOOL_WAIT):
			await _settled(job)
		if job.cancelled():  # by whoever submitted it: the model reads it
			raise ToolException(f"{tool.name} failed: its task was cancelled")
		error = job.exception()
		if error is not None:  # the model reads the failure
			raise ToolException(
				f"{tool.name} failed: {type(error).__name__}: {error}"[:500]
			) from error
		value = job.result()
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
		agent, scope = _agent_and_scope()
		with _agents.activity(AgentState.TOOL_CALL):
			try:
				value = await runtime.call_service(
					tool, agent, call_id, kwargs, scope=scope
				)
			except RuntimeError:
				# Set up wrong: no service client, or one not started. As with
				# a task tool, the agent fails rather than tell its model.
				raise
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
