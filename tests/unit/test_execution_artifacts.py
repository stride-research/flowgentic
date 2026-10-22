"""
Unit tests for LangraphIntegration.generate_execution_artifacts.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from flowgentic.langGraph.main import LangraphIntegration


@pytest.mark.parametrize("generate_report", [True, False])
async def test_generate_report_flag(generate_report, tmp_path):
	"""The report is written exactly when generate_report is True."""
	integration = LangraphIntegration(backend=None)
	integration.agent_introspector = Mock()
	integration.utils = Mock()
	integration.utils.render_graph = AsyncMock()

	await integration.generate_execution_artifacts(
		Mock(),
		str(tmp_path / "main.py"),
		final_state={},
		generate_report=generate_report,
	)

	assert integration.agent_introspector.generate_report.called is generate_report
	integration.utils.render_graph.assert_awaited_once()
