<p align="center">
  <a href="https://opensource.org/licenses/MIT">
    <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT">
  </a>
  <a href="https://www.python.org/downloads/">
    <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg" alt="Python 3.11, 3.12, 3.13">
  </a>
  <a href="https://github.com/stride-research/flowgentic/actions/workflows/continuous_integration.yml">
    <img src="https://github.com/stride-research/flowgentic/actions/workflows/continuous_integration.yml/badge.svg?branch=main" alt="CI">
  </a>
  <a href="https://github.com/stride-research/flowgentic/actions/workflows/docs-deployment.yml">
    <img src="https://github.com/stride-research/flowgentic/actions/workflows/docs-deployment.yml/badge.svg" alt="Documentation">
  </a>
</p>

# Flowgentic

Flowgentic runs LLM agents on HPC resources with the RADICAL stack: as
AsyncFlow workflow tasks, and, from 0.5.0, as LangGraph agents at scale whose
tools run as RHAPSODY tasks and whose state lives in RaDEX.
Documentation: [stride-research.github.io/flowgentic](https://stride-research.github.io/flowgentic/).

## Status

The agents-at-scale path, new in 0.5.0, is in active development. It runs many
thousands of LangGraph agents per process on an HPC allocation: an agent host,
a checkpointer that keeps agents' state in the allocation's memory, tools that
run inline, as RHAPSODY tasks or through a shared MCP tool service, and a
client for a fleet of model servers.

The AsyncFlow path is in maintenance mode: it gets fixes, not features. The
LangGraph execution adapter (`LangGraphIntegration`) is frozen. New
applications can bind agents with `bind_agent`, or run LangGraph on AsyncFlow
directly.

0.5.1 requires Python 3.11 or later, and installs each path's dependencies as
an extra.

| Path | Agent framework | Runs tasks on | Python |
| --- | --- | --- | --- |
| Agents at scale | LangGraph 1.x | RHAPSODY 0.7 | 3.11, 3.12, 3.13 |
| AsyncFlow | LangGraph 1.x | RADICAL AsyncFlow 0.7, local backends | 3.11, 3.12, 3.13 |

[Project status](https://stride-research.github.io/flowgentic/status/) lists the tested versions, the known limitations
and the archived branches.

## What it provides

Agents at scale (see [LangGraph agents at scale](https://stride-research.github.io/flowgentic/guides/agents-at-scale/)):

- **`AgentHost`** (`flowgentic.host`) runs thousands of agents in one process:
  it tunes the collector, caps the agents live at once, staggers their starts,
  resumes agents that wait on tasks, and counts agents by state.
- **`KeyValueSaver`** (`flowgentic.langgraph`) keeps agents' checkpoints in a
  key-value store (`flowgentic.store`): RaDEX on an allocation, a dictionary in
  the process elsewhere.
- **`task_tool` and `mcp_tool`** (`flowgentic.tools`) say where a tool runs: as
  a RHAPSODY task, which may suspend its agent, or through the tool service
  (`flowgentic.tools.service`), which calls MCP tools for many agent processes.
- **`Fleet`** (`flowgentic.fleet`) sends each agent's model calls to one
  replica of a model server, under caps on tokens and requests in flight.

The AsyncFlow path:

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
- **`LangGraphIntegration`** (`flowgentic.langgraph`) runs a LangGraph graph's
  tools, services, tasks and nodes as AsyncFlow tasks, in their caller's trace.

## Install

From a clone, with the locked environment:

```bash
git clone https://github.com/stride-research/flowgentic.git
cd flowgentic
uv sync --locked
```

Flowgentic's core installs no agent framework and no workflow engine. Each
path is an extra: `langgraph`, `rhapsody` and `mcp` for agents at scale;
`langgraph` and `asyncflow` for the LangGraph integration on AsyncFlow;
`asyncflow` for `bind_agent`. The demos' `make` targets add what they need.

Or build the wheel and install it into a new environment, with the extras you
use:

```bash
uv build
uv venv --python 3.11 flowgentic-env
uv pip install --python flowgentic-env/bin/python "dist/flowgentic-0.5.1-py3-none-any.whl[langgraph,rhapsody,mcp]"
```

## Run the examples

`examples/agents-at-scale/` runs a campaign of agents on a laptop, with a
scripted model, RHAPSODY on a thread pool and a real MCP server, in one agent
process or several:

```bash
uv run --extra dev python examples/agents-at-scale/campaign.py --agents 1000
```

[LangGraph agents at scale](https://stride-research.github.io/flowgentic/guides/agents-at-scale/) walks through it.

Two demos of the AsyncFlow path, in `examples/ai-hpc-coupling/`, run offline,
with no model credentials: a deterministic LangGraph graph, and an agentic
one whose agents take scripted decisions.

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
> Oliveros, D., & Turilli, M. (2026). *Flowgentic* (Version 0.5.1) [Computer
> software]. https://github.com/stride-research/flowgentic

## License

MIT; see [LICENSE](LICENSE).
