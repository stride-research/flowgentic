# Flowgentic AI-HPC coupling demos

These paired demos implement the same paradigmatic scientific loop in two
ways:

> A surrogate-guided search stands in for active learning, screening, or
> iterative reconstruction: propose candidates, run expensive simulations,
> assimilate the evidence, and decide whether to continue.

The deterministic version makes every scientific decision with ordinary
Python. The agentic version assigns planning, evidence interpretation, and
coordination to LLM-capable agents. The comparison keeps the synthetic
objective, resident surrogate, simulation budget, convergence threshold,
retry policy, named AI/compute resources, and optional ADR controller fixed.

| Concern | Deterministic implementation | Agentic implementation |
|---|---|---|
| Candidate selection | Surrogate proposal is accepted directly | Planner interrogates the surrogate tool and returns a structured proposal |
| Simulation execution | Deterministic concurrent fan-out | Deterministic executor invokes only policy-approved simulation tools |
| Evidence interpretation | Surrogate update is copied into state | Analyst interprets the same update and makes an advisory recommendation |
| Coordination | Outer loop applies fixed rules | Supervisor selects the next strategy and recommends continue/stop |
| Hard authority | Application or ADR | Application or ADR; agent outputs are validated and cannot exceed bounds, budget, or stopping policy |

This is the architectural point of the comparison: agentic reasoning changes
the implementation of decisions, not the scientific capabilities or their
execution mapping. Flowgentic gives both graphs the same AsyncFlow execution
semantics and the same path to RADICAL resources.

## What each layer owns

1. **Application graph:** scientific state, dependencies, and the
   plan/simulate/analyze loop.
2. **Agents:** bounded, structured decisions about candidate selection,
   evidence, and next-cycle strategy.
3. **Flowgentic:** maps graph nodes, agent tools, persistent services, retries,
   and named backends onto AsyncFlow while preserving agent-level evidence.
4. **AsyncFlow/RHAPSODY:** executes AI and compute work on the selected local or
   remote resources.
5. **Application or ADR:** owns hard convergence, budget, and maximum-cycle
   policy. Adding ADR does not change either graph.

The simulation executor is deliberately not an autonomous LLM agent. It is a
small, deterministic boundary that invokes only validated tools. This keeps
resource use auditable while allowing the planner, analyst, and supervisor to
reason nondeterministically.

## Code organization

Audience-facing application code is separated from presentation scaffolding:

| File | Purpose | Show live? |
|---|---|---|
| `application.py` | Deterministic Flowgentic mappings, graph nodes, and application-owned loop | **Yes: deterministic comparison** |
| `agentic_application.py` | Planner, bounded tools, executor, analyst, supervisor, and the same graph loop | **Yes: agentic comparison** |
| `adr_control.py` | Optional ADR goals, observations, policy, and actions around either graph | When discussing ADR |
| `campaign_types.py` | Scientific settings and state shared by both implementations | Briefly |
| `agentic_support.py` | Structured decisions, live/rehearsal model adapters, policy guards, and agent evidence | Only for guardrails |
| `demo_support.py` | Synthetic science, timing, failure injection, local backends, and reporting | No |
| `flowgentic_campaign.py`, `agentic_campaign.py` | CLI setup and artifact generation | No |
| `run_demo.sh`, `run_agentic_demo.sh` | One-command launchers for the linked repositories | No |

All demo Python uses four-space indentation.

### Suggested live code walkthrough

Show the deterministic `application.py` first: persistent surrogate, three
scientific tasks, three graph nodes, and the short application-owned loop.
Then open `agentic_application.py` and show the correspondence:

1. The surrogate capabilities are exposed as bounded Flowgentic tools.
2. `planner_agent` queries the surrogate, reasons, and passes a candidate guard.
3. `simulation_executor` fans approved tools out concurrently with retry.
4. `analyst_agent` updates the same surrogate and interprets the evidence.
5. `supervisor_agent` recommends a strategy; hard policy retains authority.
6. The graph remains ordinary LangGraph composition.

Open `adr_control.py` last to show that ADR wraps either compiled graph without
modifying it.

## Run the deterministic implementation

From the Flowgentic repository root:

```bash
examples/ai-hpc-coupling/run_demo.sh application
examples/ai-hpc-coupling/run_demo.sh adr
```

## Run the agentic implementation

The default rehearsal mode is offline and repeatable. It exercises the full
planner/tool/executor/analyst/supervisor architecture with scripted structured
decisions, so a presentation does not depend on network access:

```bash
examples/ai-hpc-coupling/run_agentic_demo.sh application
examples/ai-hpc-coupling/run_agentic_demo.sh adr
```

Live mode replaces only the decision-model adapter with a nondeterministic LLM:

```bash
export OPEN_ROUTER_API_KEY=...
examples/ai-hpc-coupling/run_agentic_demo.sh application \
    --agent-mode live \
    --provider openrouter \
    --model google/gemini-2.5-flash
```

`chatopenai` and `ollama` are also accepted providers. Use a provider-appropriate
model name and credentials. Live decisions use Pydantic structured output and
still pass the same deterministic candidate and stop-policy guards.

The launchers discover the linked repositories from the current workspace
layout. For a different layout, set `ASYNCFLOW_SRC`, `ADR_SRC`, or
`FLOWGENTIC_PYTHON` explicitly.

The default run injects one transient simulation failure. Flowgentic retries it,
so the campaign completes while the summary records the retry. Pass
`--no-failure` to disable this behavior.

## Evidence and outputs

Both versions print scientific progress, resource use, stopping reason, resident
service reuse, and observed simulation concurrency. The agentic version also
prints its next strategy and records every structured agent decision together
with the applicable policy result.

The deterministic version writes to `demo_results/`; the agentic version writes
to `agentic_demo_results/`. Each contains:

- `campaign_summary.json`: scientific state, execution events, retries, and
  backends; the agentic summary additionally includes decisions and guardrails;
- `agent_execution_results/execution_summary.md`: Flowgentic's node/state
  introspection report.

The local named backends keep the demonstration laptop-safe. Replacing them
with RHAPSODY backends changes resource setup, not either application graph.
