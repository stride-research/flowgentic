"""
Flowgentic reaches AsyncFlow through one module, `flowgentic._asyncflow`.

Every other module calls the seam's functions, never an engine's own methods;
it may import AsyncFlow's types for annotations, under `if TYPE_CHECKING:`.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import flowgentic

SOURCE = Path(flowgentic.__file__).parent
SEAM = SOURCE / "_asyncflow.py"
# WorkflowEngine's methods for registering work and shutting down; the seam is
# their only caller.
ENGINE_METHODS = {"function_task", "executable_task", "prompt_task", "block", "shutdown"}


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


def _engine_calls(tree: ast.Module) -> list[int]:
	"""Line numbers of engine calls outside the seam's own functions."""
	lines = []
	for node in _runtime_nodes(tree):
		if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
			continue
		owner = node.func.value
		owner_name = owner.id if isinstance(owner, ast.Name) else None
		if owner_name == "_asyncflow":
			continue
		creates = node.func.attr == "create" and owner_name == "WorkflowEngine"
		if node.func.attr in ENGINE_METHODS or creates:
			lines.append(node.lineno)
	return sorted(lines)


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


def test_only_the_seam_calls_asyncflow() -> None:
	offenders = {}
	for module in sorted(SOURCE.rglob("*.py")):
		if module == SEAM:
			continue
		lines = _engine_calls(_parse(module))
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


def test_the_seam_calls_asyncflow() -> None:
	assert _engine_calls(_parse(SEAM))  # noqa: S101
