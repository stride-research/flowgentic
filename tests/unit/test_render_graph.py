"""
Unit tests for LangraphUtils.render_graph.

The mock app's draw_png writes a file, so pygraphviz is not needed.
"""

import logging
from pathlib import Path
from unittest.mock import Mock

from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.utils import LangraphUtils
from flowgentic.utils.telemetry.report_generator import GRAPH_IMAGE, REPORT_FILE

GRAPH_SECTION = f"# MULTI-AGENT GRAPH\n\n![Graph]({GRAPH_IMAGE})\n\n"


def mock_app() -> Mock:
	app = Mock()
	app.get_graph.return_value.draw_png.side_effect = lambda path: Path(
		path
	).write_bytes(b"PNG")
	return app


async def test_render_graph_without_a_report_writes_one(tmp_path, caplog):
	"""With no report in the directory, the graph section becomes the report."""
	with caplog.at_level(logging.WARNING, logger="flowgentic"):
		await LangraphUtils.render_graph(mock_app(), dir_to_write=str(tmp_path))

	assert [r.getMessage() for r in caplog.records] == []
	assert (tmp_path / GRAPH_IMAGE).read_bytes() == b"PNG"
	assert (tmp_path / REPORT_FILE).read_text() == GRAPH_SECTION


async def test_render_graph_prepends_to_the_report(tmp_path):
	"""An existing report keeps its content below the graph section."""
	(tmp_path / REPORT_FILE).write_text("# EXECUTION REPORT\n")

	await LangraphUtils.render_graph(mock_app(), dir_to_write=str(tmp_path))

	assert (tmp_path / GRAPH_IMAGE).read_bytes() == b"PNG"
	assert (tmp_path / REPORT_FILE).read_text() == (
		GRAPH_SECTION + "# EXECUTION REPORT\n"
	)


async def test_artifacts_without_the_report_still_render_the_graph(tmp_path):
	"""generate_report=False still leaves the graph and its section."""
	integration = LangraphIntegration(backend=None)
	integration.utils = LangraphUtils()

	await integration.generate_execution_artifacts(
		mock_app(),
		str(tmp_path / "main.py"),
		final_state={},
		generate_report=False,
		output_dir=tmp_path / "out",
	)

	assert (tmp_path / "out" / GRAPH_IMAGE).read_bytes() == b"PNG"
	assert (tmp_path / "out" / REPORT_FILE).read_text() == GRAPH_SECTION
