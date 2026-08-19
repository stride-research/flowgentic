# RADICAL and Flowgentic AI-HPC coupling demos

These demos implement the same paradigmatic scientific loop through three
software paths:

> A surrogate-guided search stands in for active learning, screening, or
> iterative reconstruction: propose candidates, run expensive simulations,
> assimilate the evidence, and decide whether to continue.

1. A **RADICAL baseline** uses ordinary Python and AsyncFlow directly.
2. A retained **deterministic LangGraph** version exercises Flowgentic without
   LLM reasoning for development and regression comparison.
3. The **agentic version** assigns planning, evidence interpretation, and
   coordination to LLM-capable LangGraph agents through Flowgentic.

The presentation compares the first and third paths. It keeps the synthetic
objective, resident surrogate, simulation budget, convergence threshold, retry
policy, named AI/compute resources, and optional ADR controller fixed.

| Concern | RADICAL baseline | Flowgentic agentic implementation |
|---|---|---|
| Programming model | Ordinary Python and AsyncFlow | LangGraph agents and tools |
| Candidate selection | Surrogate proposal is accepted directly | Planner interrogates the surrogate tool and returns a structured proposal |
| Simulation execution | Direct concurrent AsyncFlow fan-out | Deterministic executor invokes only policy-approved agent tools |
| Evidence interpretation | Surrogate update is copied into state | Analyst interprets the same update and makes an advisory recommendation |
| Coordination | Outer loop applies fixed rules | Supervisor selects the next strategy and recommends continue/stop |
| Hard authority | Application or ADR | Application or ADR; agent outputs cannot exceed bounds, budget, or stopping policy |

This is the architectural point of the comparison: RADICAL already handles the
deterministic adaptive workflow. Flowgentic becomes necessary when an agent
framework introduces messages, tool calls, agent state, handoffs, and reasoning
traces that must be translated into the same AsyncFlow execution path.

## What each layer owns

1. **Application:** scientific state, capabilities, and hard policy.
2. **Agents:** optional bounded decisions about candidate selection,
   evidence, and next-cycle strategy.
3. **Flowgentic:** only on the agentic path, maps agent nodes, tools, services,
   and reasoning operations onto AsyncFlow while preserving agent evidence.
4. **AsyncFlow/RHAPSODY:** executes AI and compute work on the selected local or
   remote resources.
5. **Application or ADR:** owns hard convergence, budget, and maximum-cycle
   policy. Adding ADR changes neither application implementation.

The simulation executor is deliberately not an autonomous LLM agent. It is a
small, deterministic boundary that invokes only validated tools. This keeps
resource use auditable while allowing the planner, analyst, and supervisor to
reason nondeterministically.

## Code organization

Audience-facing application code is separated from presentation scaffolding:

| File | Purpose | Show live? |
|---|---|---|
| `radical_application.py` | Direct AsyncFlow tasks, persistent service, concurrent simulations, and deterministic loop | **Yes: baseline** |
| `agentic_application.py` | Planner, bounded tools, executor, analyst, supervisor, and the same graph loop | **Yes: agentic comparison** |
| `application.py` | Retained deterministic LangGraph/Flowgentic implementation | No: regression comparison |
| `adr_control.py` | Optional ADR goals, observations, policy, and actions around any implementation | When discussing ADR |
| `campaign_common.py` | Shared synthetic science, settings, resources, failure injection, and reporting | No |
| `campaign_types.py` | LangGraph-specific state and application types | No |
| `agentic_support.py` | Structured decisions, live/rehearsal model adapters, policy guards, and agent evidence | Only for guardrails |
| `demo_support.py` | Flowgentic/LangGraph instrumentation and state helpers | No |
| `radical_campaign.py`, `agentic_campaign.py` | CLI setup and artifact generation | No |
| `run_radical_demo.sh`, `run_agentic_demo.sh` | Presentation launchers for the linked repositories | No |

All demo Python uses four-space indentation.

### Suggested live code walkthrough

Show `radical_application.py` first: direct AsyncFlow decorators, a resident
surrogate service, concurrent simulations, and a short deterministic loop. The
absence of Flowgentic is deliberate. Then open `agentic_application.py` and
show the correspondence:

1. The surrogate capabilities are exposed as bounded Flowgentic tools.
2. `planner_agent` queries the surrogate, reasons, and passes a candidate guard.
3. `simulation_executor` fans approved tools out concurrently with retry.
4. `analyst_agent` updates the same surrogate and interprets the evidence.
5. `supervisor_agent` recommends a strategy; hard policy retains authority.
6. The graph remains ordinary LangGraph composition.

Open `adr_control.py` last to show that ADR wraps either an ordinary cycle or a
compiled agent graph without changing its implementation.

## Run the RADICAL baseline

From the Flowgentic repository root:

```bash
examples/ai-hpc-coupling/run_radical_demo.sh application
examples/ai-hpc-coupling/run_radical_demo.sh adr
```

## Run the retained deterministic LangGraph implementation

This version remains available for Flowgentic development but is not part of
the primary presentation comparison:

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

The default run injects one transient simulation failure. The RADICAL baseline
applies the same deterministic retry policy that Flowgentic applies on the
agentic path, and both summaries record the retry. Pass `--no-failure` to
disable this behavior.

## Evidence and outputs

All versions print scientific progress, resource use, stopping reason, resident
service reuse, and observed simulation concurrency. The agentic version also
prints its next strategy and records every structured agent decision together
with the applicable policy result.

The RADICAL baseline writes to `radical_demo_results/`, the retained LangGraph
version to `demo_results/`, and the agentic version to
`agentic_demo_results/`. Each contains `campaign_summary.json` with scientific
state, execution events, retries, and backends. Flowgentic implementations also
produce:

- `agent_execution_results/execution_summary.md`: Flowgentic's node/state
  introspection report.

The local named backends keep the demonstration laptop-safe. Replacing them
with RHAPSODY backends changes resource setup, not the scientific loop or agent
graph.
