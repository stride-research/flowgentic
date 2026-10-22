# Project status

## Maintenance mode

Flowgentic 0.3.0 is a consolidation release. It preserves the July 2026 work
as a documented, tested snapshot: fixes and evidence, and no new features.

The LangGraph execution adapter (`LangraphIntegration` and its `asyncflow`
decorators) is frozen in maintenance mode and gets no new features. The
decision follows the [substitution experiment](evaluation/substitution.md),
which ran the agentic demo with and without the adapter. Without it, the demo
kept the same results, task identity, cancellation, service reuse, backend
placement, tool selection and handoffs, in about the same code and with 32
fewer third-party packages. The adapter's only distinct contribution was an
implicit retry and timeout on every call and graph node, which re-runs
scientific work.

`bind_agent`, which binds an agent component to an AsyncFlow task, remains a
documented recipe: [Augment an AsyncFlow application](guides/augment-asyncflow-app.md).
A LangGraph application can also run on AsyncFlow without Flowgentic:
[LangGraph on AsyncFlow directly](guides/langgraph-direct.md).

## Supported configuration

| Agent framework | Workflow engine | Backends | Python |
| --- | --- | --- | --- |
| LangGraph 1.x | RADICAL AsyncFlow 0.6.0 | Local | 3.10, 3.11, 3.12 |

Tested environment, from `uv.lock`: LangGraph 1.2.16, langchain-core 1.6.13,
langchain 1.4.7, radical-asyncflow 0.6.0 and academy-py 0.3.0. CI runs the
test suite on Python 3.10, 3.11 and 3.12. The package requires
`langgraph`, `langchain-core` and `langchain` at `>=1.0,<2.0`, and
`radical-asyncflow==0.6.0`.

## Known limitations

- DEBUG logging is on by default, and `config.yml` is looked up in the working
  directory at import.
- ADR mode is not supported. The demos' `--controller adr` needs an ADR source
  checkout (`ADR_SRC`), because ADR is not released.
- Not validated: using an engine that the caller creates (the integration
  always creates its own), and any remote or HPC backend. Cancellation reaching
  in-flight AsyncFlow tasks is validated on local backends only.
- The adapter's default retry policy (3 attempts of at most 30 s each) also
  wraps graph nodes: a node that runs longer than 30 s fails, and a node that
  fails with a retryable error re-runs its tasks. See
  [Fault tolerance](features/fault_tolerance.md).
- Token accounting: the agentic demo records no tokens, even in live mode
  (substitution report, row 5).
- The design-pattern examples need a model provider and credentials.
  `tests/integration/test_integration.py` runs two of them when
  `FLOWGENTIC_RUN_LLM_INTEGRATION=1` is set (`make tests-integration-llm`);
  CI never sets it.
- Memory: the `trim_middle` and `importance_based` strategies can keep the
  wrong messages, and `summarize` can keep one message more than the limit.
  See
  [Memory](features/memory.md#trimming-strategies).
- The Academy integration (`flowgentic.academy`) has no tests, and its two
  examples in `examples/academy-integration/` fail at import.

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

The tag `snapshot/radical-demo-2026-08` marks the `demo/radical` branch at
`dd27bd8b7e6e` (19 August 2026), the July 2026 demos as they stood before
they were merged into `main`.

## Earlier releases

Tags `v0.1.0` (23 September 2025) and `0.2.0` (2 October 2025) predate the
[changelog](changelog.md).
