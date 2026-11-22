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
AsyncFlow workflow tasks, and, from 0.5.0, as agents at scale whose tools run
as RHAPSODY tasks and whose state lives in RaDEX. Agents at scale run on
Flowgentic's own agent loop and OpenAI-compatible model servers; LangGraph is
optional.
Documentation: [stride-research.github.io/flowgentic](https://stride-research.github.io/flowgentic/).

## Status

The agents-at-scale path, new in 0.5.0, is in active development. It runs many
thousands of agents per process on an HPC allocation: an agent host,
Flowgentic's own agent loop, which keeps a suspended agent's state in the
allocation's memory, tools that run inline, as RHAPSODY tasks or through a
shared MCP tool service, and a client for a fleet of OpenAI-compatible model
servers, such as vLLM and SGLang. LangGraph is optional: with the `langgraph`
extra, LangGraph graphs run on the same parts.

The AsyncFlow path is in maintenance mode: it gets fixes, not features. The
LangGraph execution adapter (`LangGraphIntegration`) is frozen. New
applications can bind agents with `bind_agent`, or run LangGraph on AsyncFlow
directly.

0.6.0 requires Python 3.11 or later, and installs each path's dependencies as
an extra.

| Path | Agent framework | Runs tasks on | Python |
| --- | --- | --- | --- |
| Agents at scale | Flowgentic's agent loop; LangGraph 1.x, optional | RHAPSODY 0.7 | 3.11, 3.12, 3.13 |
| AsyncFlow | LangGraph 1.x | RADICAL AsyncFlow 0.7, local backends | 3.11, 3.12, 3.13 |

[Project status](https://stride-research.github.io/flowgentic/status/) lists the tested versions, the known limitations
and the archived branches.

## What it provides

Agents at scale (see [Agents at scale](https://stride-research.github.io/flowgentic/guides/agents-at-scale/)):

- **`LoopRunner`** (`flowgentic.loop`) runs each agent as a tool-calling loop
  on OpenAI-format messages: the calls of a reply run at once, tool errors go
  back to the model, and an agent suspends while its task runs, its state in a
  key-value store (`flowgentic.store`): RaDEX on an allocation, a dictionary
  in the process elsewhere. It needs no LangGraph.
- **`AgentHost`** (`flowgentic.host`) runs thousands of agents in one process:
  it tunes the collector, caps the agents live at once, staggers their starts,
  resumes agents that wait on tasks, and counts agents by state.
- **`GraphRunner` and `KeyValueSaver`** (`flowgentic.langgraph`), with the
  `langgraph` extra, run LangGraph graphs in the same host, their checkpoints
  in the same store.
- **`task_tool` and `mcp_tool`** (`flowgentic.tools`) say where a tool runs: as
  a task on a RHAPSODY session or on the application's own AsyncFlow engine,
  which may suspend its agent and leaves a record of when the agent decided
  on it and when it ran, or through the tool service
  (`flowgentic.tools.service`), which calls MCP tools for many agent processes.
- **`Fleet`** (`flowgentic.fleet`) sends each agent's model calls to one
  replica of a model server, under caps on tokens and requests in flight. A
  replica is a transport, such as `HTTPTransport` for vLLM, SGLang or
  OpenRouter.
- **`read_export`** (`flowgentic.telemetry`) reads back the OpenTelemetry
  export of every agent process of a run, and checks it: each fleet's calls
  against its `chat` spans, and its tokens against the model servers' own
  counters.
- **`configure`** (`flowgentic.otlp`), with the `otlp` extra, streams each
  agent process's metrics and spans to an OpenTelemetry collector, and
  `examples/live-dashboard` shows the run live in Grafana: agents in each
  state, model calls and tokens per second, queues at the replicas, tools.
  Its stack runs per user, on 127.0.0.1, behind a login and a token; on a
  shared node, other users of the node can reach 127.0.0.1, so keep both.

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
path is an extra: `agents` for agents at scale (`rhapsody` and `mcp`), with
`langgraph` to run LangGraph graphs on them; `langgraph` and `asyncflow` for
the LangGraph integration on AsyncFlow; `asyncflow` for `bind_agent`; `otlp`
to stream a run's telemetry to an OpenTelemetry collector, for a live
dashboard. The demos' `make` targets add what they need.

Or build the wheel and install it into a new environment, with the extras you
use:

```bash
uv build
uv venv --python 3.11 flowgentic-env
uv pip install --python flowgentic-env/bin/python "dist/flowgentic-0.6.0-py3-none-any.whl[agents]"
```

## Run the examples

`examples/agents-at-scale/` runs a campaign of agents on Flowgentic's loop on
a laptop, with a scripted model, RHAPSODY on a thread pool and a real MCP
server, in one agent process or several; `--runner graph` runs the same
agents as a LangGraph graph:

```bash
uv run --extra agents python examples/agents-at-scale/campaign.py --agents 1000
uv run --extra agents --extra langgraph python examples/agents-at-scale/campaign.py --agents 1000 --runner graph
```

[Agents at scale](https://stride-research.github.io/flowgentic/guides/agents-at-scale/) walks through it.

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
> Oliveros, D., & Turilli, M. (2026). *Flowgentic* (Version 0.6.0) [Computer
> software]. https://github.com/stride-research/flowgentic

## License

MIT; see [LICENSE](LICENSE).
