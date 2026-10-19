"""Guard for the closure invariant that cost a day of debugging.

A Dragon task body that references a module imported in the PARENT captures the whole
module object in its closure, and the backend then has to serialize it to the child. For
CageFlow -- which transitively pulls in PyRosetta, numpy and torch -- the child never comes
back: the task shows as submitted and running, and its future never resolves.

This caught `CageFlow.DEFAULT_CROSS_CONTACT_DIST`, referenced inside `task_geometry` purely
to read one float default. Resolve such constants in the parent and capture them by value.
"""
from __future__ import annotations

import ast
from pathlib import Path

MAIN = Path(__file__).resolve().parent.parent / "main.py"

#: Modules main() imports into parent scope. Referencing any of these inside a task body
#: ships the module object itself.
PARENT_MODULES = {
    "CageFlow", "records", "postredesign_selection", "rosetta_scoring",
    "symmdef", "native_gaps", "_pds",
}


def _task_functions(tree: ast.AST):
    """Yield every function decorated with @flow.function_task."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            if any("function_task" in ast.unparse(d) for d in node.decorator_list):
                yield node


def _names_bound_inside(fn: ast.AST) -> set[str]:
    """Names the body binds itself (own imports, own assignments) -- those are safe."""
    bound = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Import):
            bound.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            bound.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
    return bound


def find_module_captures(source: str) -> list[str]:
    """Every parent-module attribute access inside a task body, as 'task: Mod.attr (line N)'."""
    tree = ast.parse(source)
    violations = []
    for fn in _task_functions(tree):
        safe = _names_bound_inside(fn) | {a.arg for a in fn.args.args}
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                mod = node.value.id
                if mod in PARENT_MODULES and mod not in safe:
                    violations.append(f"{fn.name}: {mod}.{node.attr} (line {node.lineno})")
    return sorted(set(violations))


def test_no_task_captures_a_parent_module():
    found = find_module_captures(MAIN.read_text())
    assert not found, (
        "Task bodies must not reference parent-scope modules (it ships the module object "
        "to the Dragon child and the task never returns). Import inside the body, or "
        "resolve the value in the parent:\n  " + "\n  ".join(found)
    )


if __name__ == "__main__":
    bad = find_module_captures(MAIN.read_text())
    print("\n".join(bad) if bad else "OK: no task body captures a parent-scope module")
    raise SystemExit(1 if bad else 0)
