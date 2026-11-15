"""Flowgentic for LangGraph.

Two paths:

- **Agents at scale, on RHAPSODY and RaDEX** (needs the ``langgraph`` extra,
  and ``rhapsody`` and ``mcp`` for task and MCP tools): ``GraphRunner`` runs a
  graph's agents in a ``flowgentic.host.AgentHost``; ``KeyValueSaver`` keeps
  their checkpoints in RaDEX; ``bind_tools`` turns ``flowgentic.tools``
  declarations into LangChain tools; ``route_tool_calls`` sends each tool call
  to the tool node as its own task.
- **Graphs on AsyncFlow** (needs the ``langgraph`` and ``asyncflow`` extras):
  ``LangraphIntegration`` runs a graph's tools, services, tasks and nodes as
  AsyncFlow tasks. It is imported only when used.
"""

from typing import Any

from flowgentic.langgraph.checkpoint import KeyValueSaver
from flowgentic.langgraph.runner import (
	PENDING_KEY,
	GraphRunner,
	route_tool_calls,
	tool_call_router,
)
from flowgentic.langgraph.tools import bind_tools

__all__ = [
	"PENDING_KEY",
	"BaseLLMAgentState",
	"ExecutionWrappersLangraph",
	"GraphRunner",
	"KeyValueSaver",
	"LangraphIntegration",
	"bind_tools",
	"route_tool_calls",
	"tool_call_router",
]

_ASYNCFLOW = {
	"LangraphIntegration": "flowgentic.langgraph.main",
	"ExecutionWrappersLangraph": "flowgentic.langgraph.execution_wrappers",
	"BaseLLMAgentState": "flowgentic.langgraph.execution_wrappers",
}


def __getattr__(name: str) -> Any:
	module = _ASYNCFLOW.get(name)
	if module is None:
		raise AttributeError(f"module 'flowgentic.langgraph' has no attribute {name!r}")
	try:
		import importlib

		value = getattr(importlib.import_module(module), name)
	except ImportError as exc:
		raise ImportError(
			f"flowgentic.langgraph.{name} runs graphs on AsyncFlow: install "
			"flowgentic[langgraph,asyncflow]"
		) from exc
	globals()[name] = value
	return value
