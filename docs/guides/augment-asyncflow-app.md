# Augment an AsyncFlow application

This recipe adds agent decisions to an application that already runs on
AsyncFlow, without rewriting it as an agent graph. The application keeps its
scientific state, its tasks and its hard policies; an agent is called at a
fixed decision point, as one more AsyncFlow task. `flowgentic.agent.bind_agent`
makes that call.

The recipe comes from the agent-augmented demo,
`examples/ai-hpc-coupling/augmented_application.py`, which adds a planner, an
analyst and a supervisor to the RADICAL baseline. Its run is tested in
`tests/integration/test_ai_hpc_augmented_demo.py`, and `bind_agent` in
`tests/unit/test_agent_adapter.py`.

!!! note "Under ADR"
    An application controlled by ADR takes the same decision in ADR's `@decide`
    instead. ADR is not released, and Flowgentic 0.3.0 does not support ADR
    mode.

## 1. Keep the application's tasks on AsyncFlow

The application's service and tasks stay ordinary AsyncFlow code, each on a
named backend:

```python
--8<-- "examples/ai-hpc-coupling/augmented_application.py:direct-tasks"
```

## 2. Write the agent component

An agent component is any object with an `async ainvoke(context)` method that
returns a mapping or a Pydantic model. Inside it, the component can use any
framework. In the demo each role is a private, one-node LangGraph graph:

```python
--8<-- "examples/ai-hpc-coupling/augmented_agents.py:agent-component"
```

```python
--8<-- "examples/ai-hpc-coupling/augmented_agents.py:build-component"
```

`decide` is the role's decision model: scripted in rehearsal mode, an LLM with
structured output in live mode.

## 3. Bind each component to an AsyncFlow task

`bind_agent(flow, component, identity=..., framework=..., backend=...)`
registers the component as an AsyncFlow function task on the named backend
(`"ai"` by default) and returns an `AgentTask`:

```python
--8<-- "examples/ai-hpc-coupling/augmented_application.py:bind-agents"
```

Each call to `AgentTask.ainvoke(context)` submits one AsyncFlow task and returns
a dictionary:

| Key | Content |
| --- | --- |
| `agent` | The `identity` given to `bind_agent` |
| `framework` | The `framework` label given to `bind_agent` |
| `decision` | The component's output as a plain mapping (a Pydantic model is dumped with `model_dump()`) |
| `event` | `name` (`agent.<identity>`), `backend`, `cycle` (the context's `cycle`, or -1), `started`, `finished` and `duration` |

A component that returns anything other than a mapping or a Pydantic model
raises `TypeError`.

## 4. Call the agent at the decision point

The application builds the agent's context, calls it, and then checks its
decision against its own hard policy before acting on it:

```python
--8<-- "examples/ai-hpc-coupling/augmented_application.py:decision-point"
```

`enforce_candidate_policy` returns the accepted candidates: the application,
not the agent, decides what runs. The analyst and supervisor are called the
same way later in the cycle.

## Placement on a named backend

The task runs on the backend named in `bind_agent`. This test binds a component
to `ai` and checks that the task ran there and not on `compute`:

```python
--8<-- "tests/unit/test_agent_adapter.py:bind-agent-on-backend"
```

The task's name is `agent_<identity>`, with hyphens replaced by underscores.

## Run the demo

```bash
uv run --frozen --extra examples examples/ai-hpc-coupling/run_augmented_demo.sh application
```

```text
Agent-augmented RADICAL AI-HPC coupling demo
application/ADR -> AsyncFlow <-> Flowgentic <-> agent components
controller=application  agent_mode=rehearsal  model=rehearsal-agents
target=2.75  budget=24

persistent AI service started: surrogate-service-01 (model loads=1)
cycle= 1  controller=application  simulations=4  retries=0  best_x=4.0000  uncertainty=2.2000  budget=4/24
         agents: next=explore  recommendation=continue planner=Use the balanced strategy with a surrogate-informed batch while respect…
cycle= 2  controller=application  simulations=4  retries=1  best_x=3.2667  uncertainty=1.2100  budget=8/24
         agents: next=explore  recommendation=continue planner=Use the explore strategy with a surrogate-informed batch while respecti…
cycle= 3  controller=application  simulations=4  retries=0  best_x=2.8633  uncertainty=0.6655  budget=12/24
         agents: next=balanced recommendation=continue planner=Use the explore strategy with a surrogate-informed batch while respecti…
cycle= 4  controller=application  simulations=4  retries=0  best_x=2.6415  uncertainty=0.3660  budget=16/24
         agents: next=exploit  recommendation=continue planner=Use the balanced strategy with a surrogate-informed batch while respect…
cycle= 5  controller=application  simulations=4  retries=0  best_x=2.7696  uncertainty=0.2013  budget=20/24
         agents: next=exploit  recommendation=stop     planner=Use the exploit strategy with a surrogate-informed batch while respecti…

Proof points
  application and scientific cycle remain direct AsyncFlow
  LangGraph is confined to the three agent components
  Flowgentic binds only agent components to the ai backend
  observed compute parallelism: 4
  guarded agent decisions recorded: 15
  stopped because: scientific uncertainty goal reached
```

The summary is written to
`examples/ai-hpc-coupling/augmented_demo_results/campaign_summary.json`.
Compare it with the baseline,
`examples/ai-hpc-coupling/run_radical_demo.sh application`: both reach the
same uncertainty after 20 simulations.
