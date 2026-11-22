---
title: Flowgentic
---

# Flowgentic

Flowgentic runs LLM agents on HPC resources with the RADICAL stack: as
AsyncFlow workflow tasks, and, from 0.5.0, as agents at scale whose tools run
as RHAPSODY tasks and whose state lives in RaDEX. Agents at scale run on
Flowgentic's own agent loop and OpenAI-compatible model servers; LangGraph is
optional. This
documentation describes Flowgentic 0.6.0.

## Status

The agents-at-scale path, new in 0.5.0, is in active development. It runs many
thousands of agents per process on an HPC allocation: an agent host,
Flowgentic's own agent loop, which keeps a suspended agent's state in the
allocation's memory, tools that run inline, as RHAPSODY tasks or through a
shared MCP tool service, and a client for a fleet of OpenAI-compatible model
servers, such as vLLM and SGLang. LangGraph is optional: with the `langgraph`
extra, LangGraph graphs run on the same parts.
[Agents at scale](guides/agents-at-scale.md) shows them together, in an
example that runs on a laptop.

The AsyncFlow path is in maintenance mode: it gets fixes, not features. The
LangGraph execution adapter (`LangGraphIntegration`) is frozen: the
[substitution experiment](evaluation/substitution.md) found that, in the
agentic demo, its only distinct contribution beyond AsyncFlow and LangGraph
was an implicit retry and timeout on every call and graph node, which re-ran
scientific work; 0.4.0 removed them. New applications can bind agents with
`bind_agent`, or run LangGraph on AsyncFlow directly.

0.6.0 requires Python 3.11 or later, and installs each path's dependencies as
an extra.

| Path | Agent framework | Runs tasks on | Python |
| --- | --- | --- | --- |
| Agents at scale | Flowgentic's agent loop; LangGraph 1.x, optional | RHAPSODY 0.7 | 3.11, 3.12, 3.13 |
| AsyncFlow | LangGraph 1.x | RADICAL AsyncFlow 0.7, local backends | 3.11, 3.12, 3.13 |

[Project status](status.md) lists the tested versions, the known limitations
and the archived branches.

## What it provides

Agents at scale (see [Agents at scale](guides/agents-at-scale.md)):

- **`LoopRunner`** (`flowgentic.loop`) runs each agent as a tool-calling loop
  on OpenAI-format messages: the calls of a reply run at once, tool errors go
  back to the model, and an agent suspends while its task runs, its state in a
  key-value store (`flowgentic.store`): RaDEX on an allocation, a dictionary
  in the process elsewhere. It needs no LangGraph. See
  [Agent loop](features/agent-loop.md).
- **`AgentHost`** (`flowgentic.host`) runs thousands of agents in one process:
  it tunes the collector, caps the agents live at once, staggers their starts,
  resumes agents that wait on tasks, and counts agents by state. See
  [Agent host](features/agent-host.md).
- **`GraphRunner` and `KeyValueSaver`** (`flowgentic.langgraph`), with the
  `langgraph` extra, run LangGraph graphs in the same host, their checkpoints
  in the same store. See [Checkpointer](features/checkpointer.md).
- **`task_tool` and `mcp_tool`** (`flowgentic.tools`) say where a tool runs: as
  a task on a RHAPSODY session or on the application's own AsyncFlow engine,
  which may suspend its agent and leaves a record of when the agent decided
  on it and when it ran, or through the tool service
  (`flowgentic.tools.service`), which calls MCP tools for many agent processes.
  See [Tool placement](features/tools.md) and
  [Tool service](features/tool-service.md).
- **`Fleet`** (`flowgentic.fleet`) sends each agent's model calls to one
  replica of a model server, under caps on tokens and requests in flight. A
  replica is a transport, such as `HTTPTransport` for vLLM, SGLang or
  OpenRouter. See [Fleet](features/fleet.md).
- **`read_export`** (`flowgentic.telemetry`) reads back the OpenTelemetry
  export of every agent process of a run, and checks it: each fleet's calls
  against its `chat` spans, and its tokens against the model servers' own
  counters. See [Telemetry](features/telemetry.md).
- **`configure`** (`flowgentic.otlp`), with the `otlp` extra, streams each
  agent process's metrics and spans to an OpenTelemetry collector, and
  `examples/live-dashboard` shows the run live in Grafana: agents in each
  state, model calls and tokens per second, queues at the replicas, tools.
  Its stack runs per user, on 127.0.0.1, behind a login and a token; on a
  shared node, other users of the node can reach 127.0.0.1, so keep both.
  See [Live dashboard](features/live-dashboard.md).

The AsyncFlow path:

- **`bind_agent`** (`flowgentic.agent`) binds an agent component, any object
  with an `ainvoke(context)` method, to an AsyncFlow task on a named backend.
  Each call to the bound agent runs the component as that task and returns its
  structured decision with the agent's identity and timing, and is an
  OpenTelemetry `invoke_agent` span. See [Augment an AsyncFlow application](guides/augment-asyncflow-app.md).
- **`accept`** (`flowgentic.acceptance`) accepts or rejects an agent's output
  on explicit checks, before it becomes HPC work. It asks again with the
  reasons, within an attempt budget, and falls back to a deterministic value
  when no proposal passes. Each call, attempt and check is recorded in the
  run's trace. See [Acceptance](features/acceptance.md).
- **`LangGraphIntegration`** (`flowgentic.langgraph`) runs a LangGraph graph's
  tools, services, tasks and nodes as AsyncFlow tasks, in their caller's trace.
  See [LangGraph on AsyncFlow with Flowgentic](guides/langgraph-on-asyncflow.md)
  and [Telemetry](features/telemetry.md).
  The same graph runs on AsyncFlow without Flowgentic:
  [LangGraph on AsyncFlow directly](guides/langgraph-direct.md).

## Where it fits in RADICAL

An agent can sit in six places in a RADICAL application. Flowgentic has a part
in three of them.

| Composition | Owner | Flowgentic part |
| --- | --- | --- |
| An agent proposes a decision: LangGraph inside ADR `@decide`, or an AuCaM `CampaignController` | ADR, AuCaM | None |
| A policy submits scientific work while `@decide` runs | None: it bypasses ADR's bounds, budgets and action IDs | None |
| An existing application calls an agent at fixed decision points, as an AsyncFlow task | The application | `bind_agent` |
| An ADR action or AuCaM workflow launches an agent workflow whose tools run through AsyncFlow | AuCaM, AsyncFlow | `LangGraphIntegration` (frozen) |
| An external harness (Claude Code, Codex, OpenClaw) steers a run or submits a campaign | ADR, AuCaM | None |
| A campaign of many agents, each with its tools as RHAPSODY tasks, its model on the allocation and its state in the allocation's memory | The application | `AgentHost` and the parts of agents at scale |

ADR and AuCaM are not released, and Flowgentic 0.6.0 does not support ADR
mode; see [Project status](status.md).

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

[Getting started](getting-started.md) runs the two offline demos of the
AsyncFlow path, and [Agents at scale](guides/agents-at-scale.md) the example
of agents at scale.
