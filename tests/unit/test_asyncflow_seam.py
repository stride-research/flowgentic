"""
Flowgentic reaches AsyncFlow through one module, `flowgentic._asyncflow`.

Every other module calls the seam's functions; it may import AsyncFlow's types
for annotations, under `if TYPE_CHECKING:`.
"""

import ast
from pathlib import Path

import flowgentic

SOURCE = Path(flowgentic.__file__).parent
SEAM = SOURCE / "_asyncflow.py"


def _is_type_checking(test: ast.expr) -> bool:
	return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
		isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
	)


def _imports_asyncflow(node: ast.stmt) -> bool:
	if isinstance(node, ast.Import):
		return any(alias.name.startswith("radical.asyncflow") for alias in node.names)
	if isinstance(node, ast.ImportFrom):
		if (node.module or "").startswith("radical.asyncflow"):
			return True
		return node.module == "radical" and any(
			alias.name == "asyncflow" for alias in node.names
		)
	return False


def _runtime_asyncflow_imports(tree: ast.Module) -> list[int]:
	"""Line numbers of AsyncFlow imports outside `if TYPE_CHECKING:` blocks."""
	lines = []
	pending = list(tree.body)
	while pending:
		node = pending.pop()
		if _imports_asyncflow(node):
			lines.append(node.lineno)
		elif isinstance(node, ast.If) and _is_type_checking(node.test):
			pending.extend(node.orelse)
		else:
			pending.extend(
				child for child in ast.iter_child_nodes(node) if isinstance(child, ast.stmt)
			)
	return sorted(lines)


def test_only_the_seam_imports_asyncflow() -> None:
	offenders = {}
	for module in sorted(SOURCE.rglob("*.py")):
		if module == SEAM:
			continue
		lines = _runtime_asyncflow_imports(ast.parse(module.read_text()))
		if lines:
			offenders[str(module.relative_to(SOURCE))] = lines

	assert offenders == {}  # noqa: S101


def test_the_seam_imports_asyncflow() -> None:
	assert _runtime_asyncflow_imports(ast.parse(SEAM.read_text()))  # noqa: S101
