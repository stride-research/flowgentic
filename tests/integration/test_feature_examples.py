"""Run the offline feature example that the documentation site includes.

The Services page shows code from this example, so the example runs here, in
CI, without credentials.
"""

# ruff: noqa: S101

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]


def run_example(module: str) -> str:
    """Run an example module from the repository root and return its stdout."""
    completed = subprocess.run(
        [sys.executable, "-m", module],
        cwd=REPOSITORY,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_service_task_example() -> None:
    output = run_example("examples.langgraph-integration.service-task.service-intermittent")

    # One cold start per service call; requests to a running service reuse it.
    assert output.count("COLD START: Initializing new server") == 2
    assert "Request counter incremented: 1 → 2 → 3" in output
    assert "Different instances: True" in output
    assert "Server 1: 4 total requests (counter at 4)" in output
    assert "Server 2: 2 total requests (counter at 2)" in output
