"""Flowgentic for LangGraph.

Two paths:

- **Agents in an agent host** (the ``langgraph`` extra): ``GraphRunner`` runs a
  compiled graph's agents in a ``flowgentic.host.AgentHost``, and
  ``route_tool_calls`` sends each tool call to the tool node as its own task.
- **Graphs on AsyncFlow** (the ``langgraph`` and ``asyncflow`` extras):
  ``LangraphIntegration`` runs a graph's tools, services, tasks and nodes as
  AsyncFlow tasks. It is imported when first used.

Without the extras a path needs, using it raises an error that names them.
"""

import importlib
from typing import Any

try:
	from flowgentic.langgraph.runner import (
		PENDING_KEY,
		GraphRunner,
		record_interrupt,
		route_tool_calls,
		tool_call_router,
	)
except ModuleNotFoundError as exc:
	# Other distributions can provide the langgraph namespace without langgraph.graph.
	if exc.name not in {"langgraph", "langgraph.graph"}:
		raise
	raise ModuleNotFoundError(
		"flowgentic.langgraph needs LangGraph: install flowgentic[langgraph], "
		"with flowgentic[asyncflow] too for LangraphIntegration",
		name=exc.name,
	) from exc

__all__ = [
	"PENDING_KEY",
	"BaseLLMAgentState",
	"ExecutionWrappersLangraph",
	"GraphRunner",
	"LangraphIntegration",
	"record_interrupt",
	"route_tool_calls",
	"tool_call_router",
]

_ASYNCFLOW_PATH = {
	"LangraphIntegration": "flowgentic.langgraph.main",
	"ExecutionWrappersLangraph": "flowgentic.langgraph.execution_wrappers",
	"BaseLLMAgentState": "flowgentic.langgraph.execution_wrappers",
}

_EXTRA_MODULES = (
	"langgraph",
	"langgraph.graph",  # Other distributions can provide the langgraph namespace.
	"langchain",
	"langchain_core",
	"langchain_openai",
	"langchain_ollama",
	"radical",
	"radical.asyncflow",
)


def __getattr__(name: str) -> Any:
	module = _ASYNCFLOW_PATH.get(name)
	if module is None:
		raise AttributeError(f"module 'flowgentic.langgraph' has no attribute {name!r}")
	try:
		value = getattr(importlib.import_module(module), name)
	except ModuleNotFoundError as exc:
		if exc.name not in _EXTRA_MODULES:
			raise
		raise ModuleNotFoundError(
			"flowgentic.langgraph needs LangGraph and AsyncFlow: install "
			"flowgentic[langgraph,asyncflow]",
			name=exc.name,
		) from exc
	globals()[name] = value
	return value
