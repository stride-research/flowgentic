# LangGraph agents at scale

This guide is for campaigns of many LangGraph agents, thousands to hundreds of
thousands, on an HPC allocation. Each agent's tools run as HPC tasks, its model
is served on the allocation, and its state lives in the allocation's memory.

Flowgentic is not published on PyPI. Install it from a clone, with this path's
extras:

```bash
git clone https://github.com/stride-research/flowgentic.git
cd flowgentic
uv sync --locked --extra langgraph --extra rhapsody --extra mcp
```

Or install a wheel, attached to a
[release](https://github.com/stride-research/flowgentic/releases) or built
from a clone with `uv build`, into your own environment:

```bash
uv pip install "dist/flowgentic-0.5.0-py3-none-any.whl[langgraph,rhapsody,mcp]"
```

RaDEX, which keeps agents' state in a Dragon distributed dictionary, is built
from source with its C++ core: see
[radical-cybertools/radex](https://github.com/radical-cybertools/radex). A
laptop needs neither RaDEX nor Dragon: the worked example below runs without
them.

## This path or the AsyncFlow path

Flowgentic has two paths for agents on HPC resources.

- **The AsyncFlow path** puts agents inside a workflow. A workflow
  application calls an agent at fixed decision points, as an AsyncFlow task
  ([Augment an AsyncFlow application](augment-asyncflow-app.md)), or a
  LangGraph graph runs its tools and nodes as AsyncFlow tasks
  ([LangGraph on AsyncFlow with Flowgentic](langgraph-on-asyncflow.md)). The
  workflow is in charge, and there are a few agents. This path is in
  maintenance mode.
- **This path** makes the agents the campaign. Each agent is a LangGraph
  thread, many thousands of them share each Python process, and the process
  is built for that: its collector, its event loop, its tools, its model calls
  and its checkpoints. Use it when the number of agents, not the workflow, is
  what the run is about.

## The agent process

An agent process runs one `AgentHost`, which runs agents of one graph, up to
`max_live_agents` at once:

```text
AgentHost.run(agents)  starts agents in batches, tunes the collector, resumes
│                      suspended agents, counts agents by state
└─ each agent: a LangGraph thread
   ├─ model node  ──► Fleet ──► admission per replica ──► model server
   ├─ tool node   ──► inline tool: in this process
   │              ──► task tool: a RHAPSODY task in this process's session
   │              ──► MCP tool: the tool service, through a supervised pool
   └─ checkpointer ─► KeyValueSaver ──► RadexStore, or MemoryStore
```

The application starts everything else: the agent processes, the model
servers, the dictionary and the tool service. Flowgentic creates none of them,
so the same agent code runs on a laptop and on an allocation.

## The parts

- **[Agent host](../features/agent-host.md)** (`flowgentic.host`). Runs
  agents through a framework's runner, `flowgentic.langgraph.GraphRunner` for
  LangGraph. It tunes the cyclic collector while it runs, caps the agents live
  at once, staggers the first wave's starts, resumes agents that wait on
  tasks, and counts agents by state. One agent's failure never stops the
  others.
- **[Tool placement](../features/tools.md)** (`flowgentic.tools`). Each tool
  says where it runs: inline, as a RHAPSODY task (`task_tool`), or through the
  tool service (`mcp_tool`). A task tool may suspend its agent while the task
  runs. A call made again, as LangGraph does when an agent resumes, gets the
  first call's result and runs nothing.
  `flowgentic.langgraph.route_tool_calls` sends each tool call to the tool
  node as its own task, so a resumed agent runs no other call again.
- **[Tool service](../features/tool-service.md)**
  (`flowgentic.tools.service`). One process calls MCP tools for many agent
  processes over queues, through a pool of MCP clients that keeps a failed
  connection from cancelling other calls.
- **[Checkpointer](../features/checkpointer.md)**
  (`flowgentic.langgraph.KeyValueSaver`). Keeps agents' checkpoints in a
  key-value store: RaDEX on an allocation, a dictionary in the process
  elsewhere. With `GraphRunner`'s `exit` durability, an agent writes a
  checkpoint only when it suspends and when it ends.
- **[Fleet](../features/fleet.md)** (`flowgentic.fleet`). Sends each agent's
  model calls to one replica, so its prompt stays in that replica's prefix
  cache, and admits calls while the tokens of live context in flight stay under
  a cap.

## A worked example

`examples/agents-at-scale/campaign.py` runs a whole campaign on a laptop. Each
agent looks its candidate up through an MCP tool, runs a simulation as a
RHAPSODY task and suspends while it runs, takes a note inline, and answers.
Everything around Flowgentic's parts stands in for an allocation: a scripted
model behind a fleet of two replicas, RHAPSODY on a thread pool, an in-memory
store, the tool service in a thread, and an MCP server in a process of its own
on 127.0.0.1.

```bash
uv run --extra dev python examples/agents-at-scale/campaign.py --agents 1000
uv run --extra dev python examples/agents-at-scale/campaign.py --agents 1000 --processes 2
```

With `--processes 2`, two agent processes each run a host on half the agents,
and both share the one tool service over `multiprocessing` queues, as agent
processes share it over Dragon's queues on an allocation. CI runs both forms
(`tests/integration/test_agents_at_scale.py`).

The tools: one of each kind.

```python
--8<-- "examples/agents-at-scale/campaign.py:tools"
```

The graph calls the fleet directly from its model node, with the agent's id,
and sends each tool call to the tool node as its own task:

```python
--8<-- "examples/agents-at-scale/campaign.py:graph"
```

Each agent process builds its parts. On an allocation, the session runs on a
Dragon backend, the store is `RadexStore.attach(descriptor)`, the replicas are
chat models of the model servers, and the queues are Dragon's:

```python
--8<-- "examples/agents-at-scale/campaign.py:process"
```

And its host, which builds each agent's input as a slot opens:

```python
--8<-- "examples/agents-at-scale/campaign.py:host"
```

`--json` prints the whole summary, with each process's `HostReport` figures,
the fleet's, the tools' and the checkpointer's counters.

## Choosing the limits

The host's report says where the agents spent their time, and what running
them cost the process. Read it on a run of your own agents, at the scale of
one process, before choosing the limits.

These figures come from the example with 1,000 agents on a laptop (an Apple
M4 Max, 14 cores, 36 GB of memory, Python 3.11). Each column is one run of
three, the one with the median time:

| | One agent process | Two agent processes |
| --- | --- | --- |
| Agents done | 1,000 of 1,000 | 1,000 of 1,000 |
| The hosts ran for | 5.4 s | 1.9 s |
| Agent-seconds working (`WORKING`) | 184 | 583 |
| in a tool call, the lookup (`TOOL_CALL`) | 2,499 | 362 |
| waiting for model admission (`MODEL_QUEUED`) | 133 | 275 |
| at the model (`MODEL_REQUEST`) | 126 | 200 |
| waiting for a task (`TOOL_WAIT`) | 10 | 65 |
| Share of the agents' lifetimes accounted for | 100.00 % | 100.00 % |
| Event loop lag, time-weighted p99 and maximum | 114 and 167 ms | 19 and 26 ms |
| Collector pauses, in all | 0.14 s | 0.21 s |

- **Agent-seconds by state** (`report.states`). Every agent is in one state at
  a time, so working, waiting and making model requests add up to the agents'
  lifetimes; in these runs, to within 0.001 %. In one process, the agents
  spent most of their time in `TOOL_CALL`, waiting for the lookup: the tool
  service shared the process, and its interpreter lock, with a thousand
  agents. With the service in a process of its own, as on an allocation, that
  time fell from about 2,500 to under 400 agent-seconds, and the agents'
  own work (`WORKING`) became the largest share. Read the states to find what
  to move, or add, first.
- **The event loop's lag** (`report.loop_lag`). How late the loop woke a
  100 ms timer: the time-weighted 99th percentile, the maximum, and the share
  of time it ran over 100 ms late. A loop that is late delays every agent in
  the process. Lower `max_live_agents` until the lag is what your agents can
  bear, then run more processes.
- **The collector's pauses** (`report.collector`). With the host's settings
  the collector paused for a fraction of a second over the whole run. If its
  pauses grow, raise `Collector(threshold=...)` or check that the freeze
  happened (`collector["freeze"]["frozen"]`).
- **Model admission.** `TokenAdmission(tokens, requests)` caps each replica's
  tokens of live context and requests in flight, across all agent processes;
  each process takes `share` of them. Set `tokens` to what the server's KV
  cache holds, and watch `MODEL_QUEUED` against `MODEL_REQUEST`: agents queued
  while the server is not full mean a cap that is too low.
- **Task workers.** Agents in `TOOL_WAIT` wait for their tasks. If they
  wait longer than the tasks run, the session's backend needs more workers.

## Running under Dragon

On an allocation the application runs under Dragon, and creates the same parts
on Dragon's resources:

- **The dictionary.** RHAPSODY's `DragonDataBackend` creates a Dragon
  distributed dictionary; its endpoint's `serialize()` gives the descriptor.
  Each agent process attaches once, with `RadexStore.attach(descriptor)`, and
  passes the store to its `KeyValueSaver`.
- **The tasks.** Each agent process creates a RHAPSODY `Session` on a Dragon
  execution backend, and passes it to its `ToolRuntime`.
- **The tool service.** One Dragon process runs `serve(requests, replies,
  pool)` on Dragon queues: one request queue, and one reply queue per agent
  process. Each agent process starts its `ToolServiceClient` on its own reply
  queue.
- **The agent processes.** Each runs one host on a disjoint range of agents
  (`run_indexed`), and a fleet with `share=1/processes` over the model
  servers' replicas.
- **The model servers** run on the allocation's GPUs, and each replica is a
  chat model of one server.

Flowgentic imports nothing from Dragon: the application passes in the queues,
the session and the store. CI runs the example without Dragon. For this
release the parts were tested under Dragon by hand: the store's contract on
RaDEX, an agent suspended by one Dragon process and resumed by another, the
tool service in a Dragon process serving two agent processes over Dragon's
queues, and the worked example adapted to Dragon, with 1,000 agents in two
processes, every suspension resumed from RaDEX. They passed under Dragon
0.15.0 on Python 3.12, with RHAPSODY at commit `2523ead`, whose native Dragon
backend is not in a release yet, and RaDEX at commit `9f9b0df`.
See [Project status](../status.md#known-limitations).

## Telemetry

Each part records OpenTelemetry spans and metrics through the API; the
application configures the SDK and its exporters, as
[Telemetry](../features/telemetry.md) describes. Each agent's run is an
`invoke_agent` span, each task or service call an `execute_tool` span, and
the agents by state, the host's loop lag and collector pauses, the fleet's
admission and the tools' durations are metrics. Each feature page lists its
own.
