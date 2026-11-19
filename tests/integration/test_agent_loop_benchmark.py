"""The agent-loop benchmark, small: both runners on the agents-at-scale example's
workload, with its real tool service and MCP server, give every agent the same
answer, and the loop touches the store once per suspension, resume and end."""

# ruff: noqa: S101

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
BENCHMARK = REPOSITORY / "benchmarks" / "agent_loop.py"


def load_benchmark() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agent_loop_benchmark", BENCHMARK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("runner", ["loop", "loop-langchain", "graph"])
def test_both_runners_answer_every_agent_of_the_example(runner: str) -> None:
    out = load_benchmark().run_once(runner, 100, 8, 0.0)
    assert out["outcomes"] == {"done": 100}
    assert out["correct"] == 100 and out["suspensions"] == 100
    assert out["model_calls"] == 300
    assert out["tasks"]["submitted"] == 100 and out["tasks"]["repeats"] == 100
    assert out["store"]["keys_left"] == 0
    if runner != "graph":
        assert out["store"]["calls"] == {"put": 100, "get": 100, "delete": 100}
        assert out["runner_stats"]["tool_errors"] == 0
