### Contributing to flowgentic

Thanks for your interest in contributing! This project targets developers building agent workflows for HPC environments.

---

### Getting started

```bash
git clone <your-fork-url>
cd flowgentic
uv sync --frozen --extra dev
uv run pre-commit install
```

---

### Development workflow

- Branch from `main` and open PRs against `main`.
- Keep changes focused and incremental. Include tests when adding features or fixing bugs.
- Follow the repository’s Python style (ruff-configured):

```bash
uv run --extra dev ruff check .
uv run --extra dev ruff format .
uv run --extra dev pytest -q
```

---

### Commit messages

- Use concise, imperative subject lines (e.g., "Add LangGraph async block helper").
- Include a short body if helpful to explain context or rationale.

---

### Testing

- Unit tests live under `tests/`.
- Add tests that demonstrate the new behavior and prevent regressions.
- Tests marked `dragon` run the agents-at-scale parts on a real Dragon runtime:
  RaDEX on a Dragon distributed dictionary, Dragon processes and queues, and
  RHAPSODY's native Dragon backend. CI skips them, since Dragon, RaDEX and that
  backend come from no package index. Run them by hand on a Linux machine
  where all three are installed, under `dragon`:

```bash
dragon tests/dragon_pytest.py -m dragon tests
```

  On a batch allocation of one node, `dragon -s` starts a single-node runtime.
  A change to a part with a Dragon-only path (`RadexStore`, the tool service's
  queues, the example's `--dragon` path) says in its PR which Dragon, RHAPSODY
  and RaDEX versions the suite passed under.

---

### Docs

- User/developer docs live under `docs/`.
- To preview:

```bash
uv run --extra dev mkdocs serve
```

---

### Code of Conduct

Be respectful and constructive. We follow the spirit of the Contributor Covenant.

---

### License

By contributing, you agree that your contributions will be licensed under the MIT license.

