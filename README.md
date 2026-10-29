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
    <img src="https://github.com/stride-research/flowgentic/actions/workflows/docs-deployment.yml/badge.svg" alt="Documentation">
  </a>
</p>

# Flowgentic

Flowgentic runs agent components and LangGraph workflows as tasks on
[RADICAL AsyncFlow](https://github.com/radical-cybertools/radical.asyncflow).
Documentation: [stride-research.github.io/flowgentic](https://stride-research.github.io/flowgentic/).

## Status

Flowgentic 0.4.0 is a repair release in maintenance mode. It removes features
that 0.3 shipped broken or unsafe, or that did not serve its purpose. It adds
`accept`, which checks an agent's output before it becomes HPC work, and it
carries the trace into the tasks it submits, so an agent's decisions, the tasks
they run in and their model calls form one OpenTelemetry trace. The LangGraph
execution adapter (`LangraphIntegration`) is frozen and gets no new features.
New applications can bind agents with `bind_agent`, or run LangGraph on
AsyncFlow directly.

| Agent framework | Workflow engine | Backends | Python |
| --- | --- | --- | --- |
| LangGraph 1.x | RADICAL AsyncFlow 0.6.0 | Local | 3.10, 3.11, 3.12 |

[Project status](https://stride-research.github.io/flowgentic/status/) lists the tested versions, the known limitations
and the archived branches.

## What it provides

- **`bind_agent`** (`flowgentic.agent`) binds an agent component, any object
  with an `ainvoke(context)` method, to an AsyncFlow task on a named backend.
  Each call to the bound agent runs the component as that task and returns its
  structured decision with the agent's identity and timing, and is an
  OpenTelemetry `invoke_agent` span.
- **`accept`** (`flowgentic.acceptance`) accepts or rejects an agent's output
  on explicit checks, before it becomes HPC work. It asks again with the
  reasons, within an attempt budget, and falls back to a deterministic value
  when no proposal passes. Each call, attempt and check is recorded in the
  run's trace.
- **`LangraphIntegration`** (`flowgentic.langGraph`) runs a LangGraph graph's
  tools, services, tasks and nodes as AsyncFlow tasks, in their caller's trace.

## Install

From a clone, with the locked environment:

```bash
git clone https://github.com/stride-research/flowgentic.git
cd flowgentic
uv sync --locked
```

Or build the wheel and install it into a new environment. The
[v0.3.1 GitHub release](https://github.com/stride-research/flowgentic/releases/tag/v0.3.1) carries the same wheel.

```bash
uv build
uv venv --python 3.10 flowgentic-env
uv pip install --python flowgentic-env/bin/python dist/flowgentic-0.3.1-py3-none-any.whl
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
> Oliveros, D., & Turilli, M. (2026). *Flowgentic* (Version 0.3.1) [Computer
> software]. https://github.com/stride-research/flowgentic

## License

MIT; see [LICENSE](LICENSE).
