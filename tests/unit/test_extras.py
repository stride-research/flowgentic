"""
What a plain install of Flowgentic can import, and what each extra adds.

Each check runs in its own interpreter, with the extras' packages made
unimportable, as on a machine where they are not installed.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

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
RHAPSODY = ["rhapsody"]


def test_the_core_needs_no_extra() -> None:
	"""Without any extra, the core imports and works."""
	result = run_without(
		LANGGRAPH + ASYNCFLOW + MCP + RHAPSODY,
		"""
		import flowgentic
		from flowgentic.acceptance import accept
		from flowgentic.store import MemoryStore, RadexStore
		from flowgentic.tools import ToolRuntime, mcp_tool, task_tool
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
		ASYNCFLOW, "from flowgentic.langgraph import LangGraphIntegration"
	)
	assert result.returncode != 0
	assert "install flowgentic[langgraph,asyncflow]" in result.stderr.splitlines()[-1]
	result = run_without(
		LANGGRAPH, "from flowgentic.langgraph import LangGraphIntegration"
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


def test_a_star_import_of_the_langgraph_parts_needs_langgraph_only() -> None:
	"""``import *`` brings what the installed extras provide: the agent host's
	parts without AsyncFlow, and the AsyncFlow integration too with it."""
	result = run_without(
		ASYNCFLOW,
		"""
		from flowgentic.langgraph import *
		print(sorted(n for n in dir() if not n.startswith("_")))
		""",
	)
	assert result.returncode == 0, result.stderr
	names = result.stdout.strip()
	assert "GraphRunner" in names and "KeyValueSaver" in names
	assert "LangGraphIntegration" not in names
	result = run_without(
		[],
		"""
		from flowgentic.langgraph import *
		print(LangGraphIntegration.__name__, GraphRunner.__name__)
		""",
	)
	assert result.returncode == 0, result.stderr
	assert result.stdout.split() == ["LangGraphIntegration", "GraphRunner"]


def test_the_checkpointer_names_its_extras() -> None:
	result = run_without(LANGGRAPH, "from flowgentic.langgraph import KeyValueSaver")
	assert result.returncode != 0
	assert "install flowgentic[langgraph]" in result.stderr


def test_the_fleet_needs_langchain_only() -> None:
	result = run_without(ASYNCFLOW + MCP, "from flowgentic.fleet import Fleet")
	assert result.returncode == 0, result.stderr
	result = run_without(LANGGRAPH, "import flowgentic.fleet")
	assert result.returncode != 0
	assert "install flowgentic[langchain-core]" in result.stderr.splitlines()[-1]


def test_the_agent_loop_needs_langchain_core_only() -> None:
	"""Flowgentic's own loop runs an agent without LangGraph, LangChain's
	agents, its providers or any other extra; without langchain-core, the loop
	imports, and a runner for a LangChain model names the extra."""
	result = run_without(
		["langgraph", "langchain", "langchain_openai", "langchain_ollama"]
		+ ASYNCFLOW
		+ MCP
		+ RHAPSODY,
		"""
		import asyncio
		from langchain_core.language_models import GenericFakeChatModel
		from langchain_core.messages import AIMessage
		from flowgentic.host import Agent, AgentHost
		from flowgentic.loop import LoopRunner

		model = GenericFakeChatModel(messages=iter([AIMessage("hi")]))
		report = asyncio.run(AgentHost(LoopRunner(model)).run([Agent("a", "go")]))
		print(report.outcomes)
		""",
	)
	assert result.returncode == 0, result.stderr
	assert result.stdout.strip() == "{'done': 1}"
	result = run_without(
		["langchain_core"],
		"""
		from flowgentic.loop import LoopRunner
		LoopRunner(object())
		""",
	)
	assert result.returncode != 0
	assert "install flowgentic[langchain-core]" in result.stderr.splitlines()[-1]


def test_the_agent_loop_runs_on_its_seams_without_langchain() -> None:
	"""The loop's core imports no LangChain: with every LangChain and LangGraph
	module blocked, it runs agents that suspend on a RHAPSODY task through a
	backend on OpenAI-format dicts (tests/unit/scale/dict_backend.py)."""
	scale = Path(__file__).resolve().parent / "scale"
	result = run_without(
		LANGGRAPH + ASYNCFLOW + MCP,
		f"""
		import asyncio, sys
		from concurrent.futures import ThreadPoolExecutor
		sys.path.insert(0, {str(scale)!r})
		from dict_backend import DictBackend, assistant
		from rhapsody import Session
		from rhapsody.backends import ConcurrentExecutionBackend
		from flowgentic.host import Agent, AgentHost
		from flowgentic.loop import LoopRunner
		from flowgentic.tools import ToolRuntime, task_tool

		def double(x: float) -> float:
			return 2 * x

		def script(messages):
			results = [m for m in messages if m["role"] == "tool"]
			if not results:
				return assistant(calls=(("c1", "double", {{"x": 2.0}}),))
			return assistant(results[0]["content"])

		async def main():
			session = Session(backends=[await ConcurrentExecutionBackend(ThreadPoolExecutor(2))])
			answers = []
			runner = LoopRunner(
				DictBackend(script),
				[task_tool(double, suspend=True)],
				runtime=ToolRuntime(session),
			)
			host = AgentHost(runner, on_done=lambda r, out: answers.append(out.messages[-1]["content"]))
			report = await host.run([Agent(f"a{{i}}", "go") for i in range(3)])
			await session.close()
			return report, answers

		report, answers = asyncio.run(main())
		loaded = sorted(m for m in sys.modules if m.split(".")[0] in {{"langchain_core", "langgraph", "langchain"}})
		print(report.outcomes, sum(a.suspensions for a in report.agents), answers, loaded)
		""",
	)
	assert result.returncode == 0, result.stderr
	assert result.stdout.strip() == "{'done': 3} 3 ['4.0', '4.0', '4.0'] []"


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


TASK = """
import asyncio
from flowgentic.tools import ToolRuntime, task_tool

def double(x):
	return 2 * x

async def main():
	await ToolRuntime(session=object()).submit(task_tool(double), "a", "c1", {"x": 1})

asyncio.run(main())
"""


def test_task_tools_name_their_extra_when_they_run() -> None:
	result = run_without(RHAPSODY, TASK)
	assert result.returncode != 0
	assert "install flowgentic[rhapsody]" in result.stderr.splitlines()[-1]
	result = run_without(["rhapsody.api"], TASK)  # a broken install, not a missing one
	assert result.stderr.splitlines()[-1] == "ModuleNotFoundError: No module named 'rhapsody.api'"


def test_langgraph_tools_need_langgraph_only() -> None:
	result = run_without(
		ASYNCFLOW + MCP + RHAPSODY,
		"""
		from flowgentic.langgraph import bind_tools
		from flowgentic.tools import ToolRuntime, task_tool
		print("ok")
		""",
	)
	assert result.returncode == 0, result.stderr


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


def test_the_package_marks_itself_typed():
	"""Type checkers read a package's annotations only if it ships ``py.typed``
	(PEP 561), and the build includes it as package data."""
	from importlib.resources import files

	assert (files("flowgentic") / "py.typed").is_file()
