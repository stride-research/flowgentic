"""Guard for the closure invariant that cost a day of debugging.

A Dragon task body that references a module (or a function imported from a module)
defined in the PARENT captures that object in its closure, and the backend then has to
serialize it to the child by reference rather than by value. For CageFlow -- which
transitively pulls in PyRosetta, numpy and torch -- the child never comes back: the task
shows as submitted and running, and its future never resolves.

This caught `CageFlow.DEFAULT_CROSS_CONTACT_DIST`, referenced inside `task_geometry` purely
to read one float default (job 24049). It also caught a second, distinct shape during the
2026-10-19 modularization: `_task_setup`, a function shared via `from common import
_task_setup` and called inside every task body. While it lived in main.py it was a
`__main__` function, which cloudpickle always pickles by value; once it moved into the
importable `common` module, cloudpickle would ship it BY REFERENCE instead, needing the
Dragon child to `import common` before the task body runs -- impossible, since putting
`src/` on the child's path is what that setup call exists to do.

THE ACTUAL RULE, which is sharper than "never share code": by-reference is fine *if the
worker can perform the import*. Site-packages is on every interpreter's sys.path by
default, so a function from a pip-INSTALLED package round-trips cleanly into a worker
(verified: a closure referencing `flowgentic.metrics` pickles to ~1 KB -- a reference,
not a copy -- and runs in a fresh interpreter that knows nothing about this repo). A
function from a loose source file shipped beside the job does not, because the worker
inherits none of the launcher's sys.path.

So the shared instrumentation lives in `flowgentic.metrics` (the `task()` decorator; see
docs/TELEMETRY.md), installed into the worker environment, and task bodies get it for
free by being wrapped with `@fg.task(...)` -- that is the supported pattern, and the one
stages 2 and 3 will keep using. There is no local shared module at all any more: the
deployment paths task bodies need are config keys, captured as plain strings by value.

`PARENT_BARE_NAME_MODULES` is therefore empty today. It is kept, not deleted, because the
bare-name check it drives is the one that would have caught the `_task_setup` case and the
original attribute-only test did not. Any future local helper module added under src/ and
referenced inside a task body should be listed there.

Scans src/main.py AND every file in src/stages/ -- the 2026-10-19 modularization split the
pipeline across files, so the invariant has to be checked everywhere a @flow.function_task
can be defined, not just in the one file that used to hold all of them.
"""
from __future__ import annotations

import ast
from pathlib import Path

FLOWGENTIC_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = FLOWGENTIC_DIR / "src"
SCANNED_FILES = [SRC_DIR / "main.py", *sorted((SRC_DIR / "stages").glob("*.py"))]

#: Modules that may be imported into a stage's run()/main()'s parent scope. Referencing any
#: of these inside a task body ships the module object itself.
PARENT_MODULES = {
    "CageFlow", "records", "postredesign_selection", "rosetta_scoring",
    "symmdef", "native_gaps", "_pds",
}

#: Local (non-installed) modules under src/ whose exports must never be referenced inside a
#: task body, by bare name, regardless of what the export is. Empty now that common.py is
#: gone; add any future local helper module here rather than trusting that its exports
#: look harmless -- a worker cannot import a loose file at all.
PARENT_BARE_NAME_MODULES: set[str] = set()


#: Substrings that identify a task-registering decorator. `fg.task(` covers the
#: `flowgentic.metrics.task()` wrapper (see docs/TELEMETRY.md), which registers via
#: `flow.function_task` internally but is spelled differently at the call site. Add a new
#: substring here, not a special case, if a third spelling shows up.
_TASK_DECORATOR_MARKERS = ("function_task", "fg.task(")


def _task_functions(tree: ast.AST):
    """Yield every function decorated as an AsyncFlow task, by any known spelling."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            decorators = [ast.unparse(d) for d in node.decorator_list]
            if any(marker in dec for dec in decorators for marker in _TASK_DECORATOR_MARKERS):
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


def _module_level_bare_imports(tree: ast.AST) -> dict[str, str]:
    """Map bound_name -> source_module for every top-level
    `from <PARENT_BARE_NAME_MODULES member> import X [as Y]` in this file.
    """
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in PARENT_BARE_NAME_MODULES:
            for alias in node.names:
                out[alias.asname or alias.name] = node.module
    return out


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


def find_bare_name_captures(source: str) -> list[str]:
    """Every bare-name reference, inside a task body, to something imported at module level
    from a PARENT_BARE_NAME_MODULES member -- as 'task: name (line N)'.
    """
    tree = ast.parse(source)
    bare_imports = _module_level_bare_imports(tree)
    violations = []
    for fn in _task_functions(tree):
        safe = _names_bound_inside(fn) | {a.arg for a in fn.args.args}
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in bare_imports and node.id not in safe:
                    mod = bare_imports[node.id]
                    violations.append(f"{fn.name}: {mod}.{node.id} (line {node.lineno})")
    return sorted(set(violations))


def test_flowgentic_is_installed_not_loose():
    """Task bodies import flowgentic.metrics, so it must be a real installed package.

    If it ever resolves from a loose path shipped next to the job instead, the by-reference
    pickle that every task body relies on would fail inside the worker -- the exact hang
    this file guards against. Imported here rather than assumed: a local pass does not
    prove the CLUSTER venv has it -- the launch script's `pip install` step does that --
    but it does catch the package being removed or renamed.
    """
    import flowgentic.metrics as m

    assert hasattr(m, "task") and hasattr(m, "compute_stats"), (
        "flowgentic.metrics is importable but missing task/compute_stats -- stage "
        "task bodies depend on both."
    )


def test_no_task_captures_a_parent_module():
    for path in SCANNED_FILES:
        found = find_module_captures(path.read_text()) + find_bare_name_captures(path.read_text())
        assert not found, (
            f"{path.relative_to(FLOWGENTIC_DIR)}: task bodies must not reference anything "
            "imported in the parent -- a module, or a name imported from one of "
            f"{sorted(PARENT_BARE_NAME_MODULES)} (it ships the object to the Dragon child and "
            "the task never returns). Import inside the body, or resolve the value in the "
            "parent and pass it as an explicit argument:\n  " + "\n  ".join(found)
        )


if __name__ == "__main__":
    bad = {
        path: find_module_captures(path.read_text()) + find_bare_name_captures(path.read_text())
        for path in SCANNED_FILES
    }
    bad = {path: v for path, v in bad.items() if v}
    if not bad:
        print("OK: no task body in any scanned file captures a parent-scope module or a bare name imported from one")
        raise SystemExit(0)
    for path, violations in bad.items():
        print(f"{path.relative_to(FLOWGENTIC_DIR)}:")
        print("\n".join(f"  {v}" for v in violations))
    raise SystemExit(1)
