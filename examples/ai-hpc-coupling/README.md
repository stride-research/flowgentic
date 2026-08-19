# RADICAL and Flowgentic AI-HPC coupling demos

These demos implement the same paradigmatic scientific loop through four
software paths:

> A surrogate-guided search stands in for active learning, screening, or
> iterative reconstruction: propose candidates, run expensive simulations,
> assimilate the evidence, and decide whether to continue.

1. A **RADICAL baseline** uses ordinary Python and AsyncFlow directly.
2. A retained **deterministic LangGraph** version exercises Flowgentic without
   LLM reasoning for development and regression comparison.
3. The **agentic version** assigns planning, evidence interpretation, and
   coordination to LLM-capable LangGraph agents through Flowgentic.
4. The **agent-augmented RADICAL version** keeps the application and all
   non-agentic work in ordinary Python and AsyncFlow while Flowgentic binds
   private LangGraph components only at three decision points.

The recommended presentation comparison is now the first and fourth paths:
the same RADICAL application first runs with deterministic policies and is then
augmented at three decision points. The third path remains available to show
the alternative in which LangGraph owns the complete cycle. All paths keep the
synthetic objective, resident surrogate, simulation budget, convergence
threshold, retry policy, named AI/compute resources, and optional ADR
controller fixed.

| Concern | RADICAL baseline | Agent-augmented RADICAL implementation |
|---|---|---|
| Programming model | Ordinary Python and AsyncFlow | The same Python/AsyncFlow cycle plus bounded agent components |
| Candidate selection | Surrogate proposal is accepted directly | Planner reviews the proposal and returns a structured, policy-checked batch |
| Simulation execution | Direct concurrent AsyncFlow fan-out | The same direct concurrent AsyncFlow fan-out and deterministic retry |
| Evidence interpretation | Surrogate update is copied into state | Analyst interprets the same update and produces an advisory recommendation |
| Coordination | Outer loop applies fixed rules | Supervisor selects the next strategy and recommends continue/stop |
| Hard authority | Application or ADR | Application or ADR; agents cannot exceed bounds, budget, or stopping policy |

The agent-augmented version offers the same bounded decisions as the agentic
version without transferring application ownership to LangGraph. It is the
recommended example when showing how an existing RADICAL application can be
augmented instead of rewritten.

This is the architectural point of the comparison: RADICAL already handles the
deterministic adaptive workflow. Flowgentic becomes necessary when an agent
framework introduces messages, tool calls, agent state, handoffs, and reasoning
traces that must be translated into the same AsyncFlow execution path.

## What each layer owns

1. **Application:** scientific state, capabilities, and hard policy.
2. **Agents:** optional bounded decisions about candidate selection,
   evidence, and next-cycle strategy.
3. **Flowgentic:** binds framework-specific agents to AsyncFlow tasks, preserving
   their identity, placement, structured decisions, and execution evidence.
4. **AsyncFlow/RHAPSODY:** executes AI and compute work on the selected local or
   remote resources.
5. **Application or ADR:** owns hard convergence, budget, and maximum-cycle
   policy. Adding ADR changes neither application implementation.

Simulation execution is deliberately not delegated to an autonomous LLM
agent. The application fans out only policy-validated candidates through
AsyncFlow. This keeps resource use auditable while allowing the planner,
analyst, and supervisor to reason nondeterministically.

## Code organization

Audience-facing application code is separated from presentation scaffolding:

| File | Purpose | Show live? |
|---|---|---|
| `radical_application.py` | Direct AsyncFlow tasks, persistent service, concurrent simulations, and deterministic loop | **Yes: baseline** |
| `agentic_application.py` | LangGraph-owned planner, tools, executor, analyst, supervisor, and graph loop | Only to contrast application ownership |
| `augmented_application.py` | Direct AsyncFlow application with Flowgentic-bound agent decision points | **Yes: augmentation comparison** |
| `augmented_agents.py` | Private LangGraph implementations of planner, analyst, and supervisor | Only to show framework isolation |
| `agent_contracts.py` | Framework-neutral structured decisions and deterministic policy guards | Only for guardrails |
| `src/flowgentic/agent.py` | Reusable Flowgentic adapter from an agent component to an AsyncFlow task | **Yes: the integration seam** |
| `application.py` | Retained deterministic LangGraph/Flowgentic implementation | No: regression comparison |
| `adr_control.py` | Optional ADR goals, observations, policy, and actions around any implementation | When discussing ADR |
| `campaign_common.py` | Shared synthetic science, settings, resources, failure injection, and reporting | No |
| `campaign_types.py` | LangGraph-specific state and application types | No |
| `agentic_support.py` | Live/rehearsal decision-model adapters and retained graph support | No |
| `demo_support.py` | Flowgentic/LangGraph instrumentation and state helpers | No |
| `radical_campaign.py`, `agentic_campaign.py`, `augmented_campaign.py` | CLI setup and artifact generation | No |
| `run_radical_demo.sh`, `run_agentic_demo.sh`, `run_augmented_demo.sh` | Presentation launchers for the linked repositories | No |

All demo Python uses four-space indentation.

### Suggested live code walkthrough

Show `radical_application.py` first: direct AsyncFlow decorators, a resident
surrogate service, concurrent simulations, and a short deterministic loop. The
absence of Flowgentic is deliberate. Then open `augmented_application.py` and
show that the same structure remains in place:

1. The surrogate and simulations remain direct AsyncFlow tasks.
2. `bind_agent` inserts planner, analyst, and supervisor components at three
   explicit decision points.
3. Candidate and stopping guards remain deterministic application policy.
4. Concurrent simulation fan-out and retry are unchanged.
5. `augmented_agents.py` contains the only LangGraph-specific implementation.
6. The application can replace LangGraph without changing its scientific loop.

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

## Run the agent-augmented RADICAL implementation

This version keeps the scientific cycle in AsyncFlow and uses Flowgentic only
to bind private agent-framework components to the named AI backend:

```bash
examples/ai-hpc-coupling/run_augmented_demo.sh application
examples/ai-hpc-coupling/run_augmented_demo.sh adr
```

Live mode uses the same provider options as the agentic implementation:

```bash
examples/ai-hpc-coupling/run_augmented_demo.sh application \
    --agent-mode live \
    --provider openrouter \
    --model google/gemini-2.5-flash
```

The launchers discover the linked repositories from the current workspace
layout. For a different layout, set `ASYNCFLOW_SRC`, `ADR_SRC`, or
`FLOWGENTIC_PYTHON` explicitly.

The default run injects one transient simulation failure. The RADICAL baseline
applies the same deterministic retry policy that Flowgentic applies on the
agentic path, and both summaries record the retry. Pass `--no-failure` to
disable this behavior.

## Evidence and outputs

All versions print scientific progress, resource use, stopping reason, resident
service reuse, and observed simulation concurrency. The agentic and
agent-augmented versions also print their next strategy and record every
structured agent decision together with the applicable policy result.

The RADICAL baseline writes to `radical_demo_results/`, the retained LangGraph
version to `demo_results/`, the agentic version to `agentic_demo_results/`, and
the agent-augmented version to `augmented_demo_results/`. Each contains
`campaign_summary.json` with scientific state, execution events, retries, and
backends. The graph-owned Flowgentic implementations also produce:

- `agent_execution_results/execution_summary.md`: Flowgentic's node/state
  introspection report.

The local named backends keep the demonstration laptop-safe. Replacing them
with RHAPSODY backends changes resource setup, not the scientific loop or agent
graph.
