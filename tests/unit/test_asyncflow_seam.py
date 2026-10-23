"""
Flowgentic reaches AsyncFlow through one module, `flowgentic._asyncflow`.

Every other module calls the seam's functions; it may import AsyncFlow's types
for annotations, under `if TYPE_CHECKING:`.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import flowgentic

SOURCE = Path(flowgentic.__file__).parent
SEAM = SOURCE / "_asyncflow.py"


def _is_type_checking(test: ast.expr) -> bool:
	return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
		isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
	)


def _imports_asyncflow(node: ast.AST) -> bool:
	if isinstance(node, ast.Import):
		return any(alias.name.startswith("radical.asyncflow") for alias in node.names)
	if isinstance(node, ast.ImportFrom):
		if (node.module or "").startswith("radical.asyncflow"):
			return True
		return node.module == "radical" and any(
			alias.name == "asyncflow" for alias in node.names
		)
	return False


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


def _runtime_asyncflow_imports(tree: ast.Module) -> list[int]:
	"""Line numbers of AsyncFlow imports outside `if TYPE_CHECKING:` blocks."""
	return sorted(
		node.lineno for node in _runtime_nodes(tree) if _imports_asyncflow(node)
	)


def _parse(module: Path) -> ast.Module:
	return ast.parse(module.read_text(encoding="utf-8"))


def test_only_the_seam_imports_asyncflow() -> None:
	offenders = {}
	for module in sorted(SOURCE.rglob("*.py")):
		if module == SEAM:
			continue
		lines = _runtime_asyncflow_imports(_parse(module))
		if lines:
			offenders[str(module.relative_to(SOURCE))] = lines

	assert offenders == {}  # noqa: S101


def test_the_seam_imports_asyncflow() -> None:
	assert _runtime_asyncflow_imports(_parse(SEAM))  # noqa: S101


def test_imports_in_except_handlers_are_found() -> None:
	source = (
		"try:\n"
		"\timport fast\n"
		"except ImportError:\n"
		"\tfrom radical.asyncflow import WorkflowEngine\n"
	)
	assert _runtime_asyncflow_imports(ast.parse(source)) == [4]  # noqa: S101
