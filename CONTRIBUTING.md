### Contributing to flowgentic

Thanks for your interest in contributing! This project targets developers building agent workflows for HPC environments.

---

### Getting started

```bash
git clone <your-fork-url>
cd flowgentic
python -m venv .venv && source .venv/bin/activate
pip install -U pip
pip install -e '.[dev]'
pre-commit install
```

---

### Development workflow

- Branch from `main` and open PRs against `main`.
- Keep changes focused and incremental. Include tests when adding features or fixing bugs.
- Follow the repository’s Python style (ruff-configured):

```bash
ruff check .
ruff format .
pytest -q
```

---

### Commit messages

- Use concise, imperative subject lines (e.g., "Add LangGraph async block helper").
- Include a short body if helpful to explain context or rationale.

---

### Testing

- Unit tests live under `tests/`.
- Add tests that demonstrate the new behavior and prevent regressions.

---

### Docs

- User/developer docs live under `docs/`.
- To preview:

```bash
pip install '.[dev]'
mkdocs serve
```

---

### Code of Conduct

Be respectful and constructive. We follow the spirit of the Contributor Covenant.

---

### License

By contributing, you agree that your contributions will be licensed under the MIT license.


