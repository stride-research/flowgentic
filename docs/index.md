---
title: Flowgentic
---

# Flowgentic

Flowgentic runs agent components and LangGraph workflows as tasks on
[RADICAL AsyncFlow](https://github.com/radical-cybertools/radical.asyncflow).
This documentation describes Flowgentic 0.3.0.

## Status

Flowgentic 0.3.0 is in maintenance mode. It preserves the July 2026 work as a
documented, tested snapshot. The LangGraph execution adapter
(`LangraphIntegration`) is frozen and gets no new features: the
[substitution experiment](evaluation/substitution.md) found that, in the
agentic demo, its only distinct contribution beyond AsyncFlow and LangGraph is
an implicit retry and timeout on every call and graph node, which re-runs
scientific work. New applications can bind agents with `bind_agent`, or run
LangGraph on AsyncFlow directly.

| Agent framework | Workflow engine | Backends | Python |
| --- | --- | --- | --- |
| LangGraph 1.x | RADICAL AsyncFlow 0.6.0 | Local | 3.10, 3.11, 3.12 |

[Project status](status.md) lists the tested versions, the known limitations
and the archived branches.

## What it provides

- **`bind_agent`** (`flowgentic.agent`) binds an agent component, any object
  with an `ainvoke(context)` method, to an AsyncFlow task on a named backend.
  Each call to the bound agent runs the component as that task and returns its
  structured decision with the agent's identity and timing. See [Augment an AsyncFlow application](guides/augment-asyncflow-app.md).
- **`LangraphIntegration`** (`flowgentic.langGraph`) runs a LangGraph graph's
  tools, services, tasks and nodes as AsyncFlow tasks, with retries, a
  node-level execution report, conversation memory and a graph that changes at
  runtime. See [LangGraph on AsyncFlow with Flowgentic](guides/langgraph-on-asyncflow.md).
  The same graph runs on AsyncFlow without Flowgentic:
  [LangGraph on AsyncFlow directly](guides/langgraph-direct.md).

## Where it fits in RADICAL

An agent can sit in five places in a RADICAL application. Flowgentic has a part
in two of them.

| Composition | Owner | Flowgentic part |
| --- | --- | --- |
| An agent proposes a decision: LangGraph inside ADR `@decide`, or an AuCaM `CampaignController` | ADR, AuCaM | None |
| A policy submits scientific work while `@decide` runs | None: it bypasses ADR's bounds, budgets and action IDs | None |
| An existing application calls an agent at fixed decision points, as an AsyncFlow task | The application | `bind_agent` |
| An ADR action or AuCaM workflow launches an agent workflow whose tools run through AsyncFlow | AuCaM, AsyncFlow | `LangraphIntegration` (frozen) |
| An external harness (Claude Code, Codex, OpenClaw) steers a run or submits a campaign | ADR, AuCaM | None |

ADR and AuCaM are not released, and Flowgentic 0.3.0 does not support ADR
mode; see [Project status](status.md).

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

[Getting started](getting-started.md) runs the two offline demos.
