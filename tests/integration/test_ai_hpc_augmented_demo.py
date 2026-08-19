"""End-to-end coverage for the agent-augmented RADICAL demo."""

# ruff: noqa: S101

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_augmented_demo_keeps_application_outside_langgraph(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    demo = repository / "examples" / "ai-hpc-coupling"
    runner = demo / "augmented_campaign.py"

    subprocess.run(
        [
            sys.executable,
            str(runner),
            "--agent-mode",
            "rehearsal",
            "--controller",
            "application",
            "--budget",
            "4",
            "--max-cycles",
            "1",
            "--no-failure",
            "--output-dir",
            str(tmp_path),
        ],
        check=True,
        cwd=repository,
        timeout=60,
    )

    summary = json.loads((tmp_path / "campaign_summary.json").read_text())
    decisions = summary["agents"]["decisions"]

    assert summary["implementation"] == "agent-augmented-radical"
    assert summary["controller"] == "application"
    assert summary["simulations"] == 4
    assert summary["agents"]["framework"] == "langgraph"
    assert summary["execution"]["named_backends"] == ["ai", "compute"]
    assert summary["execution"]["max_compute_parallelism"] == 4
    assert [decision["agent"] for decision in decisions] == [
        "planner",
        "analyst",
        "supervisor",
    ]

    application_source = (demo / "augmented_application.py").read_text()
    framework_source = (demo / "augmented_agents.py").read_text()
    assert "langgraph" not in application_source.lower()
    assert "LangraphIntegration" not in application_source
    assert "from flowgentic.agent import" in application_source
    assert "bind_agent" in application_source
    assert "from langgraph" in framework_source
