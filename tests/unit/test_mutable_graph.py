"""
Unit tests for MutableGraph's runtime changes.
"""

from typing import List, TypedDict

from flowgentic.langGraph.mutable_graph import MutableGraph


class VisitState(TypedDict, total=False):
	visited: List[str]


class ThreeNodeGraph(MutableGraph):
	"""A sequential graph over nodes a, b and c that records each visit."""

	def _register_available_nodes(self) -> None:
		for name in ("a", "b", "c"):
			self.available_nodes[name] = self._make_node(name)

	@staticmethod
	def _make_node(name: str):
		async def node(state: VisitState) -> VisitState:
			return {"visited": state.get("visited", []) + [name]}

		return node

	def _get_state_schema(self) -> type:
		return VisitState


async def test_update_graph_rejects_an_active_node():
	"""Replacing a node with one already in the graph changes nothing."""
	graph = ThreeNodeGraph(agents_manager=None, initial_active_nodes=["a", "b"])
	await graph.rebuild_graph()

	assert await graph.update_graph("a", "b") is False

	assert graph.active_node_names == ["a", "b"]
	result = await graph.run({"visited": []})
	assert result["visited"] == ["a", "b"]
