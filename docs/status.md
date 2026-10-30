# Project status

## Maintenance mode

Flowgentic 0.4.0 is a repair release in maintenance mode. 0.3.0 preserved the
July 2026 work as a documented, tested snapshot, and 0.3.1 fixed defects that
0.3.0 documented. 0.4.0 removes features that 0.3 shipped broken or unsafe, or
that did not serve Flowgentic's purpose, and adds acceptance of agent outputs
and one trace across decisions, tasks and model calls. The
[changelog](changelog.md) lists each change, and what replaces each feature it
removes.

The LangGraph execution adapter (`LangraphIntegration` and its `asyncflow`
decorators) is frozen in maintenance mode and gets no new features. The
decision follows the [substitution experiment](evaluation/substitution.md),
which ran the agentic demo with and without the adapter. Without it, the demo
kept the same results, task identity, cancellation, service reuse, backend
placement, tool selection and handoffs, in about the same code and with 32
fewer third-party packages. The adapter's only distinct contribution was an
implicit retry and timeout on every call and graph node, which re-ran
scientific work; 0.4.0 removes them.

`bind_agent`, which binds an agent component to an AsyncFlow task, remains a
documented recipe: [Augment an AsyncFlow application](guides/augment-asyncflow-app.md).
`accept`, new in 0.4.0, accepts or rejects an agent's output on explicit
checks, with re-asks and a deterministic fallback:
[Acceptance](features/acceptance.md).
A LangGraph application can also run on AsyncFlow without Flowgentic:
[LangGraph on AsyncFlow directly](guides/langgraph-direct.md). So can an
application that calls Academy agents from its tasks:
[Academy actions as AsyncFlow tasks](guides/academy-on-asyncflow.md), which
replaces the `flowgentic.academy` module that 0.4.0 removed. 0.4.0 also
removes the memory manager and the mutable graph, which served none of this.
LangChain's middleware bounds an agent's history. LangGraph routes within a
run, and a graph that should change between runs is built anew as a
`StateGraph`; see the [changelog](changelog.md).

## Supported configuration

| Agent framework | Workflow engine | Backends | Python |
| --- | --- | --- | --- |
| LangGraph 1.x | RADICAL AsyncFlow 0.6.0 | Local | 3.10, 3.11, 3.12 |

Tested environment, from `uv.lock`: LangGraph 1.2.16, langchain-core 1.7.0,
langchain 1.4.7, radical-asyncflow 0.6.0 and academy-py 1.0.0. CI runs the
test suite on Python 3.10, 3.11 and 3.12, and on Python 3.10 with the lowest
versions the dependency ranges allow; a weekly workflow runs it, and builds
the docs, on the newest releases within the ranges. The package requires
`langgraph>=1.1.5,<2.0`; `langchain-core`, `langchain`, `langchain-openai` and
`langchain-ollama` at `>=1.0,<2.0`; and `radical-asyncflow>=0.6,<0.7`, because
before 1.0 an AsyncFlow minor release may break; and `opentelemetry-api>=1.43,<2`
for its spans. Its `examples` extra adds `academy-py>=1.0,<2` for the Academy
example, and the OpenTelemetry SDK, OpenTelemetry's GenAI instrumentation for
LangChain and OpenAI (1.3b0, below 1.4) and `rhapsody-py[telemetry]` for the
demos' traces.

## Known limitations

- ADR mode is not supported. The demos' `--controller adr` needs an ADR source
  checkout (`ADR_SRC`), because ADR is not released.
- Not validated: using an engine that the caller creates (the integration
  always creates its own), and any remote or HPC backend. Cancellation reaching
  in-flight AsyncFlow tasks is validated on local backends only.
- The recipe for calling Academy agents from AsyncFlow tasks is tested with
  Academy's local exchange and AsyncFlow's local backend only; see
  [Academy actions as AsyncFlow tasks](guides/academy-on-asyncflow.md#limitations).
- AsyncFlow 0.6.0 has no per-task retry or timeout, so applications wrap the
  task calls that need them. See
  [Retries and timeouts](guides/retries-and-timeouts.md).
- Tracing (see [Telemetry](features/telemetry.md)):
    - A model call inside an AsyncFlow task is a sibling of the task's span, not
      its child.
    - AsyncFlow block spans hang from the session span, not from the caller's.
    - AsyncFlow 0.6.0 labels every task span with the default backend.
    - No cost that a provider reports is recorded.
    - Acceptance's evaluation events are log records, emitted through Python's
      OpenTelemetry Logs API, which still lives in the module
      `opentelemetry._logs`. An application sees them only once it configures
      a logger provider.
    - RHAPSODY's Dragon-vLLM endpoint reports word counts as token counts.
- Live runs of the design-pattern examples need a model provider and
  credentials. CI runs three of them with a scripted chat model in place of the
  provider (`tests/integration/test_design_patterns_offline.py`), which shows
  that they complete, not what a live model makes of them.
  `make tests-integration-llm` runs two of them live, given
  `OPEN_ROUTER_API_KEY`, and the LLM examples workflow does the same on
  GitHub when a maintainer starts it. The interactive chatbot runs in neither.

## Retired

Flowgentic no longer plans the framework × backend matrix that earlier releases announced (CrewAI, AG2 and the OpenAI Agents SDK, on Pegasus, Parsl and Academy), nor universal agent abstractions of its own.

The abstractions' prototype is kept in `archive/feat/abstractions_redesign`,
and the `AGENT_TOOL_AS_MCP` flow type in `archive/feature/mcp`; see
[Archived branches](#archived-branches).

## Archived branches

The branches inactive at the 0.3.0 consolidation are archived as annotated
tags, `archive/<branch>`, on their last commit, and deleted from GitHub. No
commit was lost.

| Tag | Commit |
| --- | --- |
| `archive/hotfix/benchmarking_redesign` | `45268b5a73e2` |
| `archive/hotfix/benchmarking-engine-and-backends` | `44d5a4132ee7` |
| `archive/experiment/exp3-benchmarking-hotfix` | `8bf7270425d6` |
| `archive/refactor/decoupling_tests` | `0940607b41ca` |
| `archive/feat/cluster_docs` | `feecf47f54b4` |
| `archive/experiment1a` | `613b536586c8` |
| `archive/coordination_overhead_plot` | `5c0905438b68` |
| `archive/feat/abstractions_redesign` | `1ec100eaeb10` |
| `archive/refactor/worload_old_interface` | `9a1d45b02d87` |
| `archive/feat/benchmarking` | `4289a01d9e25` |
| `archive/feature/telemetry` | `7ff024df1ed1` |
| `archive/feature/distributed-memory` | `40fffaf3f861` |
| `archive/feature/mcp` | `73333a4a7652` |
| `archive/feature/observe-integration` | `0b121d045415` |
| `archive/example/chemcrow` | `1972bcc0c656` |

Four more branches, `hotfix/deafult-settings`, `docs/memory`, `feature/memory`
and `features/supervisor`, were already part of `main` and were deleted
without a tag.

To work on an archived branch, fetch the tags and create a local branch from
one:

```bash
git fetch origin --tags
git switch -c feature/mcp archive/feature/mcp
```

A maintainer restores the branch on GitHub by pushing the tag's commit to it:
`git push origin 'archive/<branch>^{commit}:refs/heads/<branch>'`.

The tag `snapshot/radical-demo-2026-08` marks the July 2026 demos as they
stood before they were merged into `main`: commit `dd27bd8b7e6e` (19 August
2026), the tip of the former `demo/radical` branch, which was deleted on
22 October 2026.

## Earlier releases

Tags `v0.1.0` (23 September 2025) and `0.2.0` (2 October 2025) predate the
[changelog](changelog.md).
