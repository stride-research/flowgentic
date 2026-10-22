# Flowgentic development environment

- Use `uv` and the checked-in `uv.lock`; do not install packages into the
  Homebrew or system Python.
- Run Python commands through `uv run`. Include `--extra dev` for tests, Ruff,
  MkDocs, and pre-commit.
- Run the default suite with `uv run --extra dev pytest -q`.
- External-LLM integration tests are opt-in. Run them only when explicitly
  requested, model credentials are configured, and
  `FLOWGENTIC_RUN_LLM_INTEGRATION=1` is set.
- The AI-HPC demo launchers in `examples/ai-hpc-coupling/` use the installed
  AsyncFlow. They import a local AsyncFlow checkout only when `ASYNCFLOW_SRC` is
  set; the unsupported ADR mode needs an ADR checkout (`ADR_SRC`).
- Keep audience-facing demo Python under `examples/ai-hpc-coupling/` formatted
  with four spaces per indentation level.
