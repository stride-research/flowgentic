import os
import subprocess
import sys

import pytest


LLM_EXAMPLES = [
	"examples.langgraph-integration.design_patterns.sequential.research_agent.main",
	"examples.langgraph-integration.design_patterns.supervisor.toy.main",
]


@pytest.mark.skipif(
	os.getenv("FLOWGENTIC_RUN_LLM_INTEGRATION") != "1",
	reason=(
		"external LLM integration tests are opt-in; set "
		"FLOWGENTIC_RUN_LLM_INTEGRATION=1 and provide the model credentials"
	),
)
@pytest.mark.parametrize("module", LLM_EXAMPLES)
def test_llm_example(module: str) -> None:
	"""Run an end-to-end example in the same environment as pytest."""
	subprocess.run(
		[sys.executable, "-m", module],
		check=True,
		timeout=300,
	)

# Memory
# Services
# MCP
