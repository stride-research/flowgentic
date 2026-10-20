"""Stage modules for Track B's pipeline, one file per pipeline stage.

Each module exposes a single `async def run(...) -> PipelineState` entrypoint, called in
sequence from main.py. Task bodies inside each stage follow the same three invariants
described in main.py's module docstring (task_setup first, no parent-module capture,
history writes in the parent).
"""
