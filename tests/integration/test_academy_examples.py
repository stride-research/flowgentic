"""Run the Academy example that the Academy guide includes.

The example calls Academy agents' actions from AsyncFlow tasks without
Flowgentic, offline, with Academy's local exchange and AsyncFlow's local
backend.
"""

# ruff: noqa: S101

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Skip only when academy-py is absent: a broken install must fail, not skip.
if importlib.util.find_spec("academy") is None:
    pytest.skip("needs academy-py, from the examples extra", allow_module_level=True)

REPOSITORY = Path(__file__).resolve().parents[2]
EXAMPLES = REPOSITORY / "examples" / "academy-integration"


def run_example(name: str) -> str:
    """Run an example's run.py from the repository root and return its stdout."""
    completed = subprocess.run(
        [sys.executable, str(EXAMPLES / name / "run.py")],
        cwd=REPOSITORY,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_direct_example() -> None:
    output = run_example("direct")

    assert "Initial count: 0" in output
    assert "Final count: 3" in output
    for counter in range(1, 11):
        assert f"Counter {counter}: {counter * 2}" in output
    assert "All examples completed successfully!" in output
