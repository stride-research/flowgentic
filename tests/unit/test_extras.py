"""
What a plain install of Flowgentic can import, and what each extra adds.

Each check runs in its own interpreter, with the extras' packages made
unimportable, as on a machine where they are not installed.
"""

import subprocess
import sys
import textwrap

import pytest


def run_without(blocked: list[str], code: str) -> subprocess.CompletedProcess:
	"""Run ``code`` in a new interpreter in which ``blocked`` cannot be imported."""
	prelude = textwrap.dedent(f"""
		import sys

		class BlockImports:
		    def find_spec(self, fullname, path, target=None):
		        if fullname in {blocked!r}:
		            raise ModuleNotFoundError(f"No module named {{fullname!r}}", name=fullname)

		sys.meta_path.insert(0, BlockImports())
	""")
	return subprocess.run(
		[sys.executable, "-c", prelude + textwrap.dedent(code)],
		capture_output=True,
		text=True,
		check=False,
	)


LANGGRAPH = [
	"langgraph",
	"langchain",
	"langchain_core",
	"langchain_openai",
	"langchain_ollama",
]
ASYNCFLOW = ["radical", "radical.asyncflow"]
MCP = ["mcp", "mcp_types"]


def test_the_core_needs_no_extra() -> None:
	"""Without LangGraph, LangChain or AsyncFlow, the core imports and works."""
	result = run_without(
		LANGGRAPH + ASYNCFLOW + MCP,
		"""
		import flowgentic
		from flowgentic.acceptance import accept
		from flowgentic.store import MemoryStore, RadexStore
		from flowgentic.tools.service import ToolServiceClient
		flowgentic.configure_logging()
		MemoryStore().put("k", 1)
		print("ok")
		""",
	)
	assert result.returncode == 0, result.stderr
	assert result.stdout.strip() == "ok"


def test_the_asyncflow_path_names_its_extra() -> None:
	result = run_without(ASYNCFLOW, "import flowgentic.agent")
	assert result.returncode != 0
	assert "ModuleNotFoundError" in result.stderr
	assert "install flowgentic[asyncflow]" in result.stderr


def test_the_langgraph_integration_names_its_extras() -> None:
	result = run_without(
		ASYNCFLOW, "from flowgentic.langgraph import LangraphIntegration"
	)
	assert result.returncode != 0
	assert "install flowgentic[langgraph,asyncflow]" in result.stderr.splitlines()[-1]
	result = run_without(
		LANGGRAPH, "from flowgentic.langgraph import LangraphIntegration"
	)
	assert result.returncode != 0
	last = result.stderr.splitlines()[-1]
	assert "install flowgentic[langgraph]" in last and "flowgentic[asyncflow]" in last


def test_the_graph_runner_needs_langgraph_only() -> None:
	"""The agent host's LangGraph runner imports without AsyncFlow, and names
	its own extra without LangGraph; the host itself needs neither."""
	result = run_without(
		ASYNCFLOW,
		"""
		from flowgentic.host import AgentHost
		from flowgentic.langgraph import GraphRunner, route_tool_calls
		print("ok")
		""",
	)
	assert result.returncode == 0, result.stderr
	result = run_without(LANGGRAPH, "from flowgentic.langgraph import GraphRunner")
	assert result.returncode != 0
	assert "install flowgentic[langgraph]" in result.stderr.splitlines()[-1]
	result = run_without(LANGGRAPH + ASYNCFLOW, "import flowgentic.host; print('ok')")
	assert result.returncode == 0, result.stderr


def test_the_checkpointer_names_its_extras() -> None:
	result = run_without(LANGGRAPH, "from flowgentic.langgraph import KeyValueSaver")
	assert result.returncode != 0
	assert "install flowgentic[langgraph]" in result.stderr


def test_the_fleet_needs_langchain_only() -> None:
	result = run_without(ASYNCFLOW + MCP, "from flowgentic.fleet import Fleet")
	assert result.returncode == 0, result.stderr
	result = run_without(LANGGRAPH, "import flowgentic.fleet")
	assert result.returncode != 0
	assert "install flowgentic[langgraph]" in result.stderr.splitlines()[-1]


@pytest.mark.parametrize(
	"missing", ["langchain_core", "langchain_openai", "langchain_ollama"]
)
def test_chat_provider_names_its_extra(missing: str) -> None:
	result = run_without([missing], "from flowgentic.utils import ChatLLMProvider")
	assert result.returncode != 0
	assert "install flowgentic[langgraph]" in result.stderr.splitlines()[-1]


def test_the_mcp_pool_names_its_extra() -> None:
	for blocked in (MCP, ["mcp_types"]):
		result = run_without(blocked, "import flowgentic.tools.mcp")
		assert result.returncode != 0
		assert "install flowgentic[mcp]" in result.stderr.splitlines()[-1], blocked


def test_chat_provider_imports_without_asyncflow() -> None:
	result = run_without(ASYNCFLOW, "from flowgentic.utils import ChatLLMProvider")
	assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
	("module", "missing"),
	[
		("flowgentic.agent", "opentelemetry"),
		("flowgentic.agent", "radical.asyncflow.data"),
		("flowgentic.langgraph.main", "radical.asyncflow.data"),
		("flowgentic.langgraph", "langgraph.types"),
		("flowgentic.langgraph", "langgraph.checkpoint.base"),
		("flowgentic.utils", "langchain_core.language_models"),
		("flowgentic.fleet", "langchain_core.messages"),
		("flowgentic.tools.mcp", "mcp.client"),
		("flowgentic.tools.mcp", "anyio"),
	],
)
def test_other_import_errors_are_not_reworded(module: str, missing: str) -> None:
	result = run_without([missing], f"import {module}")
	assert result.returncode != 0
	assert (
		result.stderr.splitlines()[-1]
		== f"ModuleNotFoundError: No module named {missing!r}"
	)
