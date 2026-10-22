<img width="1920" height="1080" alt="FLOWGENTIC" src="https://github.com/user-attachments/assets/9fc877a6-1c61-4b7e-bd14-00f4befd9e27" />

<p align="center">
  <a href="https://opensource.org/licenses/MIT">
    <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg" alt="Python 3.10, 3.11, 3.12">
  </a>
  <a href="https://github.com/stride-research/flowgentic/actions/workflows/continuous_integration.yml">
    <img src="https://github.com/stride-research/flowgentic/actions/workflows/continuous_integration.yml/badge.svg?branch=main" alt="CI">
  </a>
  <a href="https://github.com/stride-research/flowgentic/actions/workflows/docs-deployment.yml">
    <img src="https://github.com/stride-research/flowgentic/actions/workflows/docs-deployment.yml/badge.svg?branch=main" alt="Documentation">
  </a>
</p>

# Flowgentic

Flowgentic runs agent components and LangGraph workflows as tasks on
[RADICAL AsyncFlow](https://github.com/radical-cybertools/radical.asyncflow).
Documentation: [stride-research.github.io/flowgentic](https://stride-research.github.io/flowgentic/).

## Status

Flowgentic 0.3.0 is in maintenance mode. It preserves the July 2026 work as a
documented, tested snapshot. The LangGraph execution adapter
(`LangraphIntegration`) is frozen and gets no new features: the
[substitution experiment](https://stride-research.github.io/flowgentic/evaluation/substitution/) found that, in the
agentic demo, its only distinct contribution beyond AsyncFlow and LangGraph is
an implicit retry and timeout on every call and graph node, which re-runs
scientific work. New applications can bind agents with `bind_agent`, or run
LangGraph on AsyncFlow directly.

| Agent framework | Workflow engine | Backends | Python |
| --- | --- | --- | --- |
| LangGraph 1.x | RADICAL AsyncFlow 0.6.0 | Local | 3.10, 3.11, 3.12 |

[Project status](https://stride-research.github.io/flowgentic/status/) lists the tested versions, the known limitations
and the archived branches.

## What it provides

- **`bind_agent`** (`flowgentic.agent`) runs an agent component, any object
  with an `ainvoke(context)` method, as an AsyncFlow task on a named backend.
  It returns the component's structured decision with the agent's identity and
  timing.
- **`LangraphIntegration`** (`flowgentic.langGraph`) runs a LangGraph graph's
  tools, services, tasks and nodes as AsyncFlow tasks, with retries, a
  node-level execution report, conversation memory and a graph that changes at
  runtime.

## Install

From a clone, with the locked environment:

```bash
git clone https://github.com/stride-research/flowgentic.git
cd flowgentic
uv sync --locked
```

Or build the wheel and install it into a new environment. The v0.3.0 GitHub
release attaches the same wheel.

```bash
uv build
uv venv --python 3.10 flowgentic-env
uv pip install --python flowgentic-env/bin/python dist/flowgentic-0.3.0-py3-none-any.whl
```

## Run the demos

Two demos in `examples/ai-hpc-coupling/` run offline, with no model
credentials: a deterministic LangGraph graph, and an agentic one whose agents
take scripted decisions.

```bash
make examples-ai-hpc-deterministic
make examples-ai-hpc-agentic
```

[Getting started](https://stride-research.github.io/flowgentic/getting-started/)
explains their output, and `examples/ai-hpc-coupling/README.md` the other
demos.

## Development

```bash
uv sync --locked --extra dev
uv run --extra dev pytest -q
uv run --extra dev mkdocs build --strict
```

[CONTRIBUTING.md](CONTRIBUTING.md) covers the environment, coding style,
tests and pull requests. Report bugs and requests as
[GitHub issues](https://github.com/stride-research/flowgentic/issues/new).
[CHANGELOG.md](CHANGELOG.md) lists the changes in each release.

## How to cite

If you use Flowgentic in your work, please cite it. [CITATION.cff](CITATION.cff)
holds the metadata, and GitHub's "Cite this repository" menu turns it into APA
or BibTeX:

> Alsaadi, A., Amirghofran, Y., Barbero, C., Cordovez, D., Dominguez, J.,
> Oliveros, D., & Turilli, M. (2026). *Flowgentic* (Version 0.3.0) [Computer
> software]. https://github.com/stride-research/flowgentic

## License

MIT; see [LICENSE](LICENSE).
