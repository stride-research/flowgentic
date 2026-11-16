"""
Flowgentic reaches RHAPSODY through one module, `flowgentic._rhapsody`.

Every other module calls the seam's functions, never a session's own methods;
it may import RHAPSODY's types for annotations, under `if TYPE_CHECKING:`.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import flowgentic

SOURCE = Path(flowgentic.__file__).parent
SEAM = SOURCE / "_rhapsody.py"
# A RHAPSODY session's methods for submitting and waiting on work; the seam is
# their only caller.
SESSION_METHODS = {"submit_tasks", "wait_tasks"}


def _is_type_checking(test: ast.expr) -> bool:
	return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
		isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
	)


def _runtime_nodes(tree: ast.Module) -> Iterator[ast.AST]:
	"""Every node except those in the body of an `if TYPE_CHECKING:` block."""
	pending: list[ast.AST] = [tree]
	while pending:
		node = pending.pop()
		yield node
		if isinstance(node, ast.If) and _is_type_checking(node.test):
			pending.extend(node.orelse)
		else:
			pending.extend(ast.iter_child_nodes(node))


def _imports_rhapsody(node: ast.AST) -> bool:
	if isinstance(node, ast.Import):
		return any(alias.name.split(".")[0] == "rhapsody" for alias in node.names)
	if isinstance(node, ast.ImportFrom):
		return (node.module or "").split(".")[0] == "rhapsody"
	return False


def _offenders(test) -> dict[str, list[int]]:
	found = {}
	for module in sorted(SOURCE.rglob("*.py")):
		if module == SEAM:
			continue
		tree = ast.parse(module.read_text(encoding="utf-8"))
		lines = sorted(node.lineno for node in _runtime_nodes(tree) if test(node))
		if lines:
			found[str(module.relative_to(SOURCE))] = lines
	return found


def _submits(node: ast.AST) -> bool:
	return (
		isinstance(node, ast.Call)
		and isinstance(node.func, ast.Attribute)
		and node.func.attr in SESSION_METHODS
	)


def test_only_the_seam_imports_rhapsody() -> None:
	assert _offenders(_imports_rhapsody) == {}  # noqa: S101


def test_only_the_seam_submits_to_rhapsody() -> None:
	assert _offenders(_submits) == {}  # noqa: S101


def test_the_seam_imports_and_submits() -> None:
	tree = ast.parse(SEAM.read_text(encoding="utf-8"))
	nodes = list(_runtime_nodes(tree))
	assert any(_imports_rhapsody(n) for n in nodes)  # noqa: S101
	assert any(_submits(n) for n in nodes)  # noqa: S101
