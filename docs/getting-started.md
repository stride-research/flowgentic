# Getting started

This page installs Flowgentic and runs the two offline demos in
`examples/ai-hpc-coupling/`. Both run on a laptop, without network services or
model credentials: AsyncFlow executes on two local backends, `ai` and
`compute`, and the agentic demo takes its decisions from a scripted
"rehearsal" model.

You need [uv](https://docs.astral.sh/uv/) and Python 3.10, 3.11 or 3.12.

## Install

From a clone, with the locked environment:

```bash
git clone https://github.com/stride-research/flowgentic.git
cd flowgentic
uv sync --locked
```

`uv sync --locked` installs the versions in `uv.lock` and fails if the lock is
out of date. Add `--extra dev` for the test and documentation tools.

To use Flowgentic as a package instead, build the wheel and install it into a
new environment. The v0.3.0 GitHub release attaches the same wheel.

```bash
uv build
uv venv --python 3.10 flowgentic-env
uv pip install --python flowgentic-env/bin/python dist/flowgentic-0.3.0-py3-none-any.whl
```

## Run the deterministic demo

The deterministic demo runs a surrogate-guided search as a LangGraph graph
through Flowgentic, with no agent reasoning:

```bash
make examples-ai-hpc-deterministic
```

```text
Flowgentic deterministic AI-HPC coupling demo
deterministic graph -> Flowgentic -> AsyncFlow -> AI/compute backends
controller=application  target=2.75  budget=24

persistent AI service started: surrogate-service-01 (model loads=1)
cycle= 1  controller=application  simulations=4  retries=0  best_x=4.0000  uncertainty=2.2000  budget=4/24
cycle= 2  controller=application  simulations=4  retries=1  best_x=3.2667  uncertainty=1.2100  budget=8/24
cycle= 3  controller=application  simulations=4  retries=0  best_x=2.8633  uncertainty=0.6655  budget=12/24
cycle= 4  controller=application  simulations=4  retries=0  best_x=2.6415  uncertainty=0.3660  budget=16/24
cycle= 5  controller=application  simulations=4  retries=0  best_x=2.7635  uncertainty=0.2013  budget=20/24

Proof points
  same resident service: surrogate-service-01 (loads=1)
  named execution backends: ai, compute
  observed compute parallelism: 4
  end-to-end state: decision -> 20 simulations -> uncertainty 0.2013
  stopped because: scientific uncertainty goal reached
```

The run also prints a warning for the injected failure: the first simulation of
the second cycle fails once and is retried. The warning names it `cycle=1`,
because cycles count from 0, and each line is printed after its cycle, so the
retry shows on the `cycle= 2` line as `retries=1`.

## Run the agentic demo

The agentic demo gives planning, evidence interpretation and coordination to
LangGraph agents. Their tools run as AsyncFlow tasks through Flowgentic:

```bash
make examples-ai-hpc-agentic
```

```text
Flowgentic agentic AI-HPC coupling demo
agent graph -> Flowgentic -> AsyncFlow -> AI/compute backends
controller=application  agent_mode=rehearsal  integration=flowgentic  model=rehearsal-agents
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
  same resident service: surrogate-service-01 (loads=1)
  bounded agent tools mapped to named backends: ai, compute
  observed compute parallelism: 4
  agent decisions recorded: 15 with policy enforcement
  end-to-end state: reason -> 20 simulations -> uncertainty 0.2013
  stopped because: scientific uncertainty goal reached
```

The application, not the agents, decides when to stop: it ends the campaign
once the uncertainty reaches 0.25 or less, or when the budget of 24
simulations or the maximum of 8 cycles runs out. The agents' recommendations pass
through deterministic policy guards first.

## Read the outputs

Each demo writes its results under `examples/ai-hpc-coupling/`:

| Demo | Summary | Node report |
| --- | --- | --- |
| Deterministic | `demo_results/campaign_summary.json` | `demo_results/agent_execution_results/execution_summary.md` |
| Agentic | `agentic_demo_results/campaign_summary.json` | `agentic_demo_results/agent_execution_results/execution_summary.md` |

`campaign_summary.json` holds the outcome (`implementation`, `controller`,
`stop_reason`, `cycles`, `simulations`, `best_x`, `best_value`,
`uncertainty`), the resident service's counters (`service`), the execution
evidence (`execution`: named backends, peak compute parallelism, retries and
one timed event per task) and, for the agentic demo, every agent decision with
its policy status (`agents`) and the integration that ran (`integration`):

```bash
uv run python -c "import json; s = json.load(open('examples/ai-hpc-coupling/agentic_demo_results/campaign_summary.json')); print({k: s[k] for k in ('implementation', 'integration', 'cycles', 'simulations', 'stop_reason')})"
```

```text
{'implementation': 'agentic', 'integration': 'flowgentic', 'cycles': 5, 'simulations': 20, 'stop_reason': 'scientific uncertainty goal reached'}
```

`execution_summary.md` is Flowgentic's node-level report: one row per node
execution, with duration, tokens, tool calls and new messages. See
[Telemetry](features/telemetry.md).

## Other runs

- `examples/ai-hpc-coupling/run_radical_demo.sh application` runs the same
  campaign on AsyncFlow alone, with no agent framework and no Flowgentic.
- `examples/ai-hpc-coupling/run_augmented_demo.sh application` keeps that
  application and adds three agent decisions through `bind_agent`; see
  [Augment an AsyncFlow application](guides/augment-asyncflow-app.md).
- `examples/ai-hpc-coupling/run_agentic_demo.sh application --integration direct`
  runs the agentic demo's graph on AsyncFlow without Flowgentic; see
  [LangGraph on AsyncFlow directly](guides/langgraph-direct.md).

Run the scripts through `uv run --frozen --extra examples`, as the `make`
targets do. Live agent decisions (`--agent-mode live`) need a model provider
and its credentials; `examples/ai-hpc-coupling/README.md` lists the options.
ADR control (`--controller adr`) is not supported in 0.3.0.
