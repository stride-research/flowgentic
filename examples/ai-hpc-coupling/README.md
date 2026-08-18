# Flowgentic AI-HPC coupling demo

This deterministic demo shows how a compact agent application can express an
articulated AI-HPC coupling pattern on the RADICAL stack:

1. A LangGraph application keeps structured agent state and graph semantics while
   Flowgentic maps its nodes and tools to AsyncFlow.
2. AI-service work and simulation work execute on separate named backends, with
   simulation fan-out, retry, and a persistent model service.
3. The same agent cycle can be controlled by the application or by
   `radical.adr`. ADR adds explicit convergence and budget goals without changing
   the agent graph.

The numerical objective is intentionally synthetic and repeatable. Its structure
matches surrogate-guided active learning, molecular screening, and iterative
AI-assisted reconstruction: propose candidates, run expensive simulations,
update a surrogate, and decide whether to continue.

## Code organization

The code is separated according to what is relevant to the scientific
application and what exists only to make the presentation deterministic:

| File | Purpose | Show live? |
|---|---|---|
| `application.py` | Scientific state, Flowgentic mappings, agent nodes, graph, and application-owned loop | **Yes** |
| `adr_control.py` | Optional ADR goals, observations, policy, and actions around the same graph | When discussing ADR |
| `flowgentic_campaign.py` | CLI runner and controller selection | No |
| `demo_support.py` | Synthetic objective, message/trace bookkeeping, timing, failure injection, local backends, and reporting | No |
| `run_demo.sh` | One-command launcher for the linked repositories | No |

All demo Python uses four-space indentation.

### Suggested live code walkthrough

Open `application.py` and show four sections:

1. `CampaignState`: the scientific, conversational, and operational state.
2. The four `@flowgentic(...)` mappings: persistent AI service, AI planning,
   compute simulation with retry, and AI model updating.
3. The three agent nodes: plan, fan out simulations, and analyze.
4. The ordinary LangGraph edges and the short application-owned convergence
   loop.

Then, if useful, open `adr_control.py` to show that ADR wraps the same compiled
graph with explicit convergence and budget goals. No agent node changes.

## Run with application-owned control

From the Flowgentic repository root:

```bash
examples/ai-hpc-coupling/run_demo.sh application
```

The launcher discovers the linked repositories from their current workspace
layout. For a different layout, set `ASYNCFLOW_SRC`, `ADR_SRC`, or
`FLOWGENTIC_PYTHON` explicitly.

The equivalent direct command is:

```bash
PYTHONPATH="$PWD/src:/Users/mturilli/github/radical/radical.asyncflow/src" \
  .direnv/venv-py311/bin/python \
  examples/ai-hpc-coupling/flowgentic_campaign.py
```

## Run the same cycle under ADR control

```bash
examples/ai-hpc-coupling/run_demo.sh adr
```

The default run injects one transient simulation failure. Flowgentic retries it,
so the campaign completes while the summary records the retry. Pass
`--no-failure` to disable this behavior.

## Output

The console output is deliberately compact. It shows, per cycle:

- the campaign controller;
- simulation fan-out and retry count;
- the best scientific result and current uncertainty;
- consumed versus available simulation budget.

At the end it prints three proof points: the model was loaded once and reused,
the AI and compute backends remained distinct, and simulations overlapped in
time. It also creates:

- `demo_results/campaign_summary.json`: machine-readable scientific and runtime
  evidence, including the execution events;
- `demo_results/agent_execution_results/execution_summary.md`: Flowgentic's
  agent-level node and state report.

The local named backends keep the demo laptop-safe. They can be replaced with
RHAPSODY backends without changing the LangGraph campaign cycle.
