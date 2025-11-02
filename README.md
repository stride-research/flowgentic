
<img width="1920" height="1080" alt="FLOWGENTIC" src="https://github.com/user-attachments/assets/9fc877a6-1c61-4b7e-bd14-00f4befd9e27" />

<p align="center">
  <a href="https://opensource.org/licenses/MIT">
    <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.9+-blue.svg" alt="Python 3.9+">
  </a>
  <a href="https://github.com/radical-cybertools/radical.asyncflow/actions/workflows/tests.yml">
    <img src="https://github.com/radical-cybertools/radical.asyncflow/actions/workflows/tests.yml/badge.svg?branch=main" alt="Tests">
  </a>
  <a href="https://github.com/radical-cybertools/radical.asyncflow/actions/workflows/docs.yml">
    <img src="https://github.com/radical-cybertools/radical.asyncflow/actions/workflows/docs.yml/badge.svg" alt="Documentation">
  </a>
  <a href="https://pypi.org/project/flowgentic/">
    <img src="https://img.shields.io/badge/PyPI-not%20available-red.svg" alt="PyPI: Not Available">
  </a>
</p>


### flowgentic

Enable agentic frameworks on HPC. This package provides building blocks to run agent workflows on High-Performance Computing (HPC) schedulers and workflow engines. It focuses on developer ergonomics and composability with modern agent frameworks.

### Get started

Read the documentation: [stride-research.github.io/flowgentic](https://stride-research.github.io/flowgentic/)

---

### What is it?

- **Purpose**: Run agentic frameworks on HPC workflow engines with minimal code changes.
- **Today**: Supports **LangGraph** integrated with **RADICAL AsyncFlow** (experimental) for concurrent execution and orchestration.
- **Roadmap**: CrewAI, AG2, OpenAI Agents SDK; and HPC workflow engines such as Pegasus and Parsl.

---

### Support Matrix

Current and planned integrations. ✅ available, 🚧 planned, 🟡 pre-release.

| Agent Framework \ HPC Engine | RADICAL AsyncFlow | Pegasus | Parsl | Academy |
|---|---:|---:|---:|---:|
| LangGraph | ✅ | 🚧 | 🚧 | 🟡
| CrewAI | 🚧 | 🚧 | 🚧 |🚧
| AG2 | 🚧 | 🚧 | 🚧 | 🚧
| OpenAI Agents SDK | 🚧 | 🚧 | 🚧 | 🚧


- **Note**: As of now, the only supported path is LangGraph → RADICAL AsyncFlow.

---

### Installation

Requirements: Python >= 3.9

```bash
pip install '.'
```

Dev extras (linting, docs, tests):

```bash
pip install '.[dev]'
```

Environment variables for LLM providers:

- **OPEN_ROUTER_API_KEY**: required if you use the OpenRouter-backed LLM provider.
- `.env` files are supported via `python-dotenv` if you call `load_dotenv()`.

```bash
export OPEN_ROUTER_API_KEY=sk-or-...
```

---

### What can I use this for?

- **HPC execution of agent workflows**: Run LangGraph graphs on HPC via RADICAL AsyncFlow.
- **Concurrent tool and agent blocks**: Offload parallelizable work to HPC backends.
- **Resilience and memory**: Combine LangGraph checkpointing with HPC retries and blocks.
- **Production-oriented patterns**: Start from examples that implement sequential patterns with typed state, tool registries, and error handling.


---

### Examples

- `examples/langgraph-integration/01-parallel-tools.py`: parallel tool usage
- `examples/langgraph-integration/04-memory-summarization.py`: memory + summarization
- `examples/langgraph-integration/design_patterns/sequential/`: sequential pattern end-to-end

---

### Documentation

Browse the docs in `docs/`:

- Architecture: `docs/architecture.md`
- Features: `docs/features/`
- LangGraph how-tos: `ai-docs/langgraph/`

If you use MkDocs locally:

```bash
pip install '.[dev]'
mkdocs serve
```

---

### Roadmap (high-level)

- Add CrewAI, AG2, OpenAI Agents SDK adapters.
- Add Pegasus and Parsl execution backends.
- Expand memory/persistence options and checkpoint stores.
- Provide production templates and more design patterns.

---

### Contributing

- Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines (environment setup, coding style, testing, and PR process).


---

### How to cite

If you use `flowgentic` in your work, please cite it. A suggested reference and a BibTeX entry are provided below.

- Dominguez, J., Amirghofran, Y., & Turilli, M. (2025). flowgentic (Version 0.1.0). MIT License. Available at https://github.com/stride-research/flowgentic

```bibtex
@software{flowgentic_2025,
  title        = {flowgentic},
  author       = {Dominguez, Javier and Amirghofran, Yousef and Turilli, Matteo},
  year         = {2025},
  version      = {0.1.0},
  url          = {https://github.com/stride-research/flowgentic},
  license      = {MIT},
  note         = {A library to enable running agentic frameworks on HPC environments.}
}
```

---

### License

MIT (see `LICENSE`).


