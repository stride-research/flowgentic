"""Flowgentic for LangGraph.

``LangraphIntegration`` runs a LangGraph graph's tools, services, tasks and
nodes as AsyncFlow tasks. It needs the ``langgraph`` and ``asyncflow`` extras:
without them, importing this package raises an error that names them.
"""

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

try:
	from .execution_wrappers import BaseLLMAgentState, ExecutionWrappersLangraph
	from .main import LangraphIntegration
except ModuleNotFoundError as exc:
	if exc.name not in _EXTRA_MODULES:
		raise
	raise ModuleNotFoundError(
		"flowgentic.langgraph needs LangGraph and AsyncFlow: install "
		"flowgentic[langgraph,asyncflow]",
		name=exc.name,
	) from exc

__all__ = ["BaseLLMAgentState", "ExecutionWrappersLangraph", "LangraphIntegration"]
