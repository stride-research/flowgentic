"""The agents-at-scale example, offline: a campaign of LangGraph agents in one
agent host, with a scripted fleet, RHAPSODY on a thread pool, an in-memory
store, and the tool service with a real MCP server on 127.0.0.1."""

import importlib.util
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "agents-at-scale" / "campaign.py"


def load_example():
    spec = importlib.util.spec_from_file_location("agents_at_scale_campaign", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_a_campaign_of_agents_finishes_and_adds_up():
    summary = await load_example().run_campaign(agents=300, workers=8)
    assert summary["finished"] == 300
    assert summary["suspensions"] == 300
    assert summary["answers"] == ["best score 0.98"]
    assert summary["tools"] == {"submitted": 300, "repeats": 300, "failed": 0, "retained": 300}
    assert summary["checkpoints"]["checkpoints_put"] == 600  # at the suspension and the end
    assert summary["tool_service"]["ok"] == {"lookup": 300}
    assert summary["fleet"]["calls"] == 900
    shares = summary["states"]["share_by_category"]
    assert abs(sum(shares.values()) - 1) < 1e-3
    assert summary["collector"]["freeze"]["frozen"] > 0
