"""
What a plain install of Flowgentic can import, and what each extra adds.

Each check runs in its own interpreter, with the extras' packages made
unimportable, as on a machine where they are not installed.
"""

import subprocess
import sys
import textwrap


def run_without(blocked: list[str], code: str) -> subprocess.CompletedProcess:
	"""Run ``code`` in a new interpreter in which ``blocked`` cannot be imported."""
	prelude = "import sys\n" + "".join(f"sys.modules[{name!r}] = None\n" for name in blocked)
	return subprocess.run(
		[sys.executable, "-c", prelude + textwrap.dedent(code)],
		capture_output=True,
		text=True,
		check=False,
	)


LANGGRAPH = ["langgraph", "langchain", "langchain_core", "langchain_openai", "langchain_ollama"]
ASYNCFLOW = ["radical", "radical.asyncflow"]


def test_the_core_needs_no_extra() -> None:
	"""Without LangGraph, LangChain or AsyncFlow, the core imports and works."""
	result = run_without(
		LANGGRAPH + ASYNCFLOW,
		"""
		import flowgentic
		from flowgentic.acceptance import accept
		flowgentic.configure_logging()
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
	for blocked in (LANGGRAPH, ASYNCFLOW):
		result = run_without(blocked, "import flowgentic.langgraph")
		assert result.returncode != 0
		assert "install flowgentic[langgraph,asyncflow]" in result.stderr, blocked


def test_other_import_errors_are_not_reworded() -> None:
	"""A missing module that is not an extra's keeps its own error."""
	result = run_without(["opentelemetry"], "import flowgentic.agent")
	assert result.returncode != 0
	assert "flowgentic[" not in result.stderr
