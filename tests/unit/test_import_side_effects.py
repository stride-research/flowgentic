"""
What `import flowgentic` and `flowgentic.configure_logging` do to a process.

Logging state is process-wide, so each check runs in its own interpreter.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import Mock

from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.utils import LangraphUtils


def run_python(code: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
	return subprocess.run(
		[sys.executable, "-c", textwrap.dedent(code)],
		cwd=cwd,
		capture_output=True,
		text=True,
		check=False,
	)


def test_import_configures_nothing() -> None:
	"""The root logger keeps its level and handlers; no thread, no atexit hook."""
	result = run_python(
		"""
		import atexit, json, logging, threading
		root = logging.getLogger()
		before = [root.level, len(root.handlers), threading.active_count(), atexit._ncallbacks()]
		import flowgentic
		after = [root.level, len(root.handlers), threading.active_count(), atexit._ncallbacks()]
		handlers = [type(h).__name__ for h in logging.getLogger("flowgentic").handlers]
		print(json.dumps({"before": before, "after": after, "handlers": handlers}))
		"""
	)
	assert result.returncode == 0, result.stderr
	state = json.loads(result.stdout)
	assert state["after"] == state["before"]
	assert state["handlers"] == ["NullHandler"]


def test_import_ignores_a_foreign_config_file(tmp_path: Path) -> None:
	"""A `config.yml` in the working directory is the application's, not ours."""
	(tmp_path / "config.yml").write_text("database:\n  host: localhost\n")

	result = run_python("import flowgentic", cwd=tmp_path)

	assert result.returncode == 0, result.stderr


def test_configure_logging_adds_one_handler_to_the_flowgentic_logger() -> None:
	"""Called twice, it leaves one stderr handler; records still reach the root."""
	result = run_python(
		"""
		import json, logging, sys
		import flowgentic

		seen_by_root = []

		class Capture(logging.Handler):
			def emit(self, record):
				seen_by_root.append(record.getMessage())

		root = logging.getLogger()
		root.addHandler(Capture())
		root_level = root.level

		flowgentic.configure_logging("DEBUG")
		flowgentic.configure_logging("INFO")

		log = logging.getLogger("flowgentic")
		streams = [h for h in log.handlers if isinstance(h, logging.StreamHandler)]
		logging.getLogger("flowgentic.example").info("ran %s", "node-a")
		logging.getLogger("flowgentic.example").debug("hidden at INFO")
		print(json.dumps({
			"streams": len(streams),
			"to_stderr": streams[0].stream is sys.stderr,
			"level": log.level,
			"root_handlers": len(root.handlers),
			"root_level_unchanged": root.level == root_level,
			"seen_by_root": seen_by_root,
		}))
		"""
	)
	assert result.returncode == 0, result.stderr
	state = json.loads(result.stdout)
	assert state == {
		"streams": 1,
		"to_stderr": True,
		"level": 20,
		"root_handlers": 1,
		"root_level_unchanged": True,
		"seen_by_root": ["ran node-a"],
	}
	assert result.stderr.count("ran node-a") == 1
	assert "INFO flowgentic.example: ran node-a" in result.stderr
	assert "hidden at INFO" not in result.stderr


def test_configure_logging_writes_to_the_given_stream() -> None:
	result = run_python(
		"""
		import io, logging
		import flowgentic

		buffer = io.StringIO()
		flowgentic.configure_logging(logging.WARNING, stream=buffer)
		logging.getLogger("flowgentic.example").warning("retrying %s", "node-a")
		print(buffer.getvalue(), end="")
		"""
	)
	assert result.returncode == 0, result.stderr
	assert "WARNING flowgentic.example: retrying node-a" in result.stdout
	assert result.stderr == ""


async def test_generate_execution_artifacts_writes_to_output_dir(
	tmp_path: Path,
) -> None:
	integration = LangraphIntegration(backend=None)
	integration.utils = LangraphUtils()
	integration.agent_introspector._all_nodes = ["node_a"]
	output_dir = tmp_path / "results"

	await integration.generate_execution_artifacts(
		Mock(),
		str(tmp_path / "app" / "main.py"),
		final_state={},
		output_dir=output_dir,
	)

	assert (output_dir / "execution_summary.md").is_file()
	assert not (tmp_path / "app").exists()


async def test_generate_execution_artifacts_defaults_beside_the_caller(
	tmp_path: Path,
) -> None:
	integration = LangraphIntegration(backend=None)
	integration.utils = LangraphUtils()
	integration.agent_introspector._all_nodes = ["node_a"]

	await integration.generate_execution_artifacts(
		Mock(), str(tmp_path / "main.py"), final_state={}
	)

	assert (tmp_path / "agent_execution_results" / "execution_summary.md").is_file()
