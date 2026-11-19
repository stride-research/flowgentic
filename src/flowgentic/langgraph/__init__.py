"""Flowgentic for LangGraph.

Two paths:

- **Agents in an agent host** (the ``langgraph`` extra): ``GraphRunner`` runs a
  compiled graph's agents in a ``flowgentic.host.AgentHost``, and
  ``route_tool_calls`` sends each tool call to the tool node as its own task.
  ``bind_tools`` turns ``flowgentic.tools`` declarations (task and MCP tools)
  into LangChain tools that run on a ``flowgentic.tools.ToolRuntime``.
  ``KeyValueSaver`` is a checkpointer that keeps agents' state in a
  ``flowgentic.store`` key-value store.
- **Graphs on AsyncFlow** (the ``langgraph`` and ``asyncflow`` extras):
  ``LangGraphIntegration`` runs a graph's tools, services, tasks and nodes as
  AsyncFlow tasks. It is imported when first used.

Without the extras a path needs, using it raises an error that names them.
"""

import importlib
import importlib.util
from typing import Any

try:
	from flowgentic.langgraph.runner import (
		PENDING_KEY,
		GraphRunner,
		record_interrupt,
		route_tool_calls,
	)
	from flowgentic.langgraph.checkpoint import KeyValueSaver
	from flowgentic.langgraph.tools import bind_tools
except ModuleNotFoundError as exc:
	# Other distributions can provide the langgraph namespace without langgraph.graph.
	if exc.name not in {
		"langgraph",
		"langgraph.graph",
		"langgraph.checkpoint",
		"langchain_core",
	}:
		raise
	raise ModuleNotFoundError(
		"flowgentic.langgraph needs LangGraph: install flowgentic[langgraph], "
		"with flowgentic[asyncflow] too for LangGraphIntegration",
		name=exc.name,
	) from exc

__all__ = [
	"PENDING_KEY",
	"GraphRunner",
	"KeyValueSaver",
	"bind_tools",
	"record_interrupt",
	"route_tool_calls",
]

_ASYNCFLOW_PATH = {
	"LangGraphIntegration": "flowgentic.langgraph.main",
	"ExecutionWrappersLangGraph": "flowgentic.langgraph.execution_wrappers",
	"BaseLLMAgentState": "flowgentic.langgraph.execution_wrappers",
}


def _asyncflow_installed() -> bool:
	try:
		return importlib.util.find_spec("radical.asyncflow") is not None
	except ModuleNotFoundError:  # no radical namespace at all
		return False


# ``import *`` imports every name in ``__all__``: the AsyncFlow integration's
# names are in it only when AsyncFlow is installed, so that a star import
# works with the ``langgraph`` extra alone.
if _asyncflow_installed():
	__all__ += sorted(_ASYNCFLOW_PATH)

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
