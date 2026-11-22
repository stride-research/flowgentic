"""The agents-at-scale example, offline: a campaign of agents on Flowgentic's
own loop in agent hosts, with a scripted fleet, RHAPSODY on a thread pool, an
in-memory store, the tool service, and a real MCP server on 127.0.0.1; and
the same campaign on LangGraph, with the ``langgraph`` extra.

The guide at docs/guides/agents-at-scale.md walks through this example and
quotes its output, so it runs here, in CI, with and without LangGraph
installed.
"""

# ruff: noqa: S101

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
EXAMPLE = REPOSITORY / "examples" / "agents-at-scale" / "campaign.py"


def load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agents_at_scale_campaign", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(summary: dict, agents: int, runner: str = "loop") -> None:
    """What every campaign of the example must show, in any number of processes."""
    assert summary["runner"] == runner
    # Every agent finishes, with the right answer, after one suspension.
    assert summary["outcomes"] == {"done": agents}
    assert summary["answers"] == {"best score 0.98": agents}
    assert summary["suspensions"] == agents
    # Three model calls each; one task each, found again on resume, not rerun.
    assert summary["model_calls"] == 3 * agents
    assert summary["tasks"]["submitted"] == agents
    assert summary["tasks"]["repeats"] == agents
    assert summary["tasks"]["failed"] == 0
    # The loop puts an agent's state in the store once, when it suspends, and
    # deletes it when the agent ends; LangGraph puts a checkpoint at the
    # suspension and one at the end.
    assert summary["state_puts"] == (1 if runner == "loop" else 2) * agents
    # One lookup each, through the one tool service.
    assert summary["tool_service"]["unique"] == agents
    assert summary["tool_service"]["ok"] == {"lookup": agents}
    # Working, waiting and making model requests account for the agents'
    # lifetimes, within 1 %.
    seconds = summary["agent_seconds"]
    assert set(seconds) == {"working", "waiting", "model_request"}
    assert all(v > 0 for v in seconds.values())
    assert abs(summary["accounted"] - 1) < 0.01, summary["accounted"]


def test_a_campaign_in_one_agent_process() -> None:
    summary = load_example().run_campaign(agents=200)
    check(summary, 200)
    (process,) = summary["per_process"]
    assert process["model_calls_per_replica"] == [300, 300]
    assert process["collector"]["freeze"]["frozen"] > 0
    assert process["tool_service_client"]["errors"] == 0


def test_the_campaign_on_langgraph() -> None:
    """``--runner graph``: the same agents as a LangGraph graph, with the
    ``langgraph`` extra."""
    pytest.importorskip("langgraph")
    summary = load_example().run_campaign(agents=200, runner="graph")
    check(summary, 200, runner="graph")
    (process,) = summary["per_process"]
    assert process["model_calls_per_replica"] == [300, 300]


@pytest.mark.parametrize("runner", ["loop", "graph"])
def test_model_latency_slows_each_model_call_on_both_runners(runner: str) -> None:
    """``--model-latency``: each of an agent's three model calls takes at
    least that long, one after the other, whichever runner."""
    if runner == "graph":
        pytest.importorskip("langgraph")
    summary = load_example().run_campaign(agents=20, runner=runner, latency_s=0.2)
    check(summary, 20, runner=runner)
    assert summary["hosts_wall_s"] >= 3 * 0.2


def test_two_agent_processes_share_one_tool_service() -> None:
    """Two agent processes, each with its own host, and the tool service in the
    parent, over multiprocessing queues. Run as a user runs it, as a script."""
    completed = subprocess.run(
        [sys.executable, str(EXAMPLE), "--agents", "200", "--processes", "2", "--json"],
        cwd=REPOSITORY,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        encoding="utf-8",
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    check(summary, 200)
    assert summary["processes"] == 2
    for process in summary["per_process"]:
        assert process["agents"] == 100
        assert process["outcomes"] == {"done": 100}
        # Each process sent its own agents' lookups to the shared service.
        assert process["tool_service_client"]["sent"] == 100
        assert process["tool_service_client"]["answered"] == 100


@pytest.mark.dragon
def test_the_campaign_on_dragon(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--dragon``: the agents' state in RaDEX on a Dragon dictionary, the
    simulations on RHAPSODY's native Dragon backend, and the tool service and
    two agent processes as Dragon processes over Dragon's queues."""
    # Imported by name, so that Dragon's processes can import it too.
    monkeypatch.syspath_prepend(str(EXAMPLE.parent))
    campaign = importlib.import_module("campaign")
    summary = campaign.run_campaign(agents=200, processes=2, workers=4, dragon=True)
    check(summary, 200)
    pids = {process["pid"] for process in summary["per_process"]}
    assert len(pids) == 2 and os.getpid() not in pids
    for process in summary["per_process"]:
        assert process["store"] == "RadexStore"
        assert process["task_backend"] == "DragonNativeExecutionBackend"
        assert process["outcomes"] == {"done": 100}
        assert process["tool_service_client"]["answered"] == 100
