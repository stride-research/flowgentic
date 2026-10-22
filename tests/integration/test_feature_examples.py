"""Run the offline feature examples that the documentation site includes.

The Mutable graph and Services pages show code from these examples, so the
examples run here, in CI, without credentials.
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
        check=True,
        timeout=120,
    )
    return completed.stdout


def test_mutable_graph_example() -> None:
    output = run_example(
        "examples.langgraph-integration.miscellaneous.mutable-graph-creation"
    )

    paths = [
        line.removeprefix("Execution Path:").strip()
        for line in output.splitlines()
        if line.startswith("Execution Path:")
    ]
    assert paths == [
        "node_a → node_b",
        "node_a → node_b → node_c",
        "node_a → node_c",
        "node_a → node_d",
        "node_a → node_f → node_d",
    ]
    assert "Conditional Result 1: decision → standard" in output
    assert "Conditional Result 2: decision → optimize" in output


def test_service_task_example() -> None:
    output = run_example("examples.langgraph-integration.service-task.service-intermittent")

    # One cold start per service call; requests to a running service reuse it.
    assert output.count("COLD START: Initializing new server") == 2
    assert "Request counter incremented: 1 → 2 → 3" in output
    assert "Different instances: True" in output
    assert "Server 1: 4 total requests (counter at 4)" in output
    assert "Server 2: 2 total requests (counter at 2)" in output
