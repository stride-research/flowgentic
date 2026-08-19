"""End-to-end coverage for the RADICAL-only presentation baseline."""

# ruff: noqa: S101

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_radical_baseline_uses_asyncflow_without_agent_dependencies(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[2]
    demo = repository / "examples" / "ai-hpc-coupling"
    runner = demo / "radical_campaign.py"

    subprocess.run(
        [
            sys.executable,
            str(runner),
            "--controller",
            "application",
            "--budget",
            "8",
            "--max-cycles",
            "2",
            "--output-dir",
            str(tmp_path),
        ],
        check=True,
        cwd=repository,
        timeout=60,
    )

    summary = json.loads((tmp_path / "campaign_summary.json").read_text())

    assert summary["implementation"] == "radical-baseline"
    assert summary["controller"] == "application"
    assert summary["simulations"] == 8
    assert summary["execution"]["named_backends"] == ["ai", "compute"]
    assert summary["execution"]["max_compute_parallelism"] == 4
    assert summary["execution"]["retries"] == 1
    assert "agents" not in summary

    baseline_sources = (
        (demo / "radical_application.py").read_text(),
        (demo / "radical_campaign.py").read_text(),
    )
    for source in baseline_sources:
        assert "from flowgentic" not in source
        assert "import flowgentic" not in source
        assert "from langgraph" not in source
        assert "import langgraph" not in source
