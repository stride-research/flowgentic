"""Flowgentic for LangGraph.

``LangraphIntegration`` runs a LangGraph graph's tools, services, tasks and
nodes as AsyncFlow tasks. ``KeyValueSaver`` is a checkpointer that keeps
agents' state in a ``flowgentic.store`` key-value store. The package needs the
``langgraph`` and ``asyncflow`` extras: without them, importing it raises an
error that names them.
"""

_EXTRA_MODULES = (
	"langgraph",
	"langgraph.graph",  # Other distributions can provide the langgraph namespace.
	"langgraph.checkpoint",
	"langchain",
	"langchain_core",
	"langchain_openai",
	"langchain_ollama",
	"radical",
	"radical.asyncflow",
)

try:
	from .checkpoint import KeyValueSaver
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

__all__ = [
	"BaseLLMAgentState",
	"ExecutionWrappersLangraph",
	"KeyValueSaver",
	"LangraphIntegration",
]
