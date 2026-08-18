"""Offline end-to-end coverage for the agentic AI-HPC presentation demo."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_agentic_rehearsal_demo_records_decisions_and_resources(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[2]
    runner = repository / "examples" / "ai-hpc-coupling" / "agentic_campaign.py"

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

    assert summary["implementation"] == "agentic"
    assert summary["controller"] == "application"
    assert summary["simulations"] == 4
    assert summary["execution"]["named_backends"] == ["ai", "compute"]
    assert summary["execution"]["max_compute_parallelism"] == 4
    assert [decision["agent"] for decision in decisions] == [
        "planner",
        "analyst",
        "supervisor",
    ]
