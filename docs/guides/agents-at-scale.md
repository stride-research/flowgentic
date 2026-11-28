# Agents at scale

This guide is for campaigns of many agents, thousands to hundreds of
thousands, on an HPC allocation. Each agent's tools run as HPC tasks, its model
is served on the allocation, and its state lives in the allocation's memory.
The agents run on Flowgentic's own agent loop, on OpenAI-compatible model
servers such as vLLM and SGLang. LangGraph is optional:
[With LangGraph](#with-langgraph) runs the same agents as a LangGraph graph.

Flowgentic is not published on PyPI. Install it from a clone, with this path's
extra, `agents` (RHAPSODY and MCP):

```bash
git clone https://github.com/stride-research/flowgentic.git
cd flowgentic
uv sync --locked --extra agents
```

Or install a wheel, attached to a
[release](https://github.com/stride-research/flowgentic/releases) or built
from a clone with `uv build`, into your own environment:

```bash
uv pip install "dist/flowgentic-0.7.0-py3-none-any.whl[agents]"
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
- **This path** makes the agents the campaign. Each agent is a loop of model
  calls and tool calls, many thousands of them share each Python process, and
  the process is built for that: its collector, its event loop, its tools, its
  model calls and its agents' state. Use it when the number of agents, not
  the workflow, is what the run is about.

## The agent process

An agent process runs one `AgentHost`, which runs agents through one runner,
up to `max_live_agents` at once: a fixed set with `run`, or one at a time, as
they arrive, with `submit` (see
[Serving agents on demand](#serving-agents-on-demand)):

```text
AgentHost.run(agents)  starts agents in batches, tunes the collector, resumes
│                      suspended agents, counts agents by state
└─ each agent: LoopRunner's loop, on OpenAI-format messages
   ├─ model call  ──► Fleet ──► admission per replica ──► transport ──► model server
   ├─ tool calls, all of a reply at once
   │              ──► inline tool: in this process
   │              ──► task tool: a RHAPSODY task in this process's session
   │              ──► MCP tool: the tool service, through a supervised pool
   └─ suspension  ──► its messages, one put ──► RadexStore, or MemoryStore
```

The application starts everything else: the agent processes, the model
servers, the dictionary and the tool service. Flowgentic creates none of them,
so the same agent code runs on a laptop and on an allocation.

## The parts

- **[Agent host](../features/agent-host.md)** (`flowgentic.host`). Runs
  agents through a runner: `flowgentic.loop.LoopRunner`, or
  `flowgentic.langgraph.GraphRunner` for LangGraph. It tunes the cyclic
  collector while it runs, caps the agents live at once, staggers a run's
  first wave, resumes agents that wait on tasks, and counts agents by
  state. One agent's failure never stops the others.
- **[Agent loop](../features/agent-loop.md)** (`flowgentic.loop`). Runs each
  agent as a tool-calling loop on OpenAI-format messages: the model replies,
  the calls of its reply run at once, their results go back to the model,
  until it answers. Tool errors go back to the model, and `max_model_calls`
  bounds an agent's calls. An agent that calls a task tool that suspends
  leaves its messages in the store with one put, and reads them back with one
  get when its task ends; the entry is deleted when the agent ends.
- **[Tool placement](../features/tools.md)** (`flowgentic.tools`). Each tool
  says where it runs: inline, as a RHAPSODY task (`task_tool`), or through the
  tool service (`mcp_tool`). A task tool may suspend its agent while the task
  runs. A call made again when its agent resumes gets the first call's result
  and runs nothing.
- **[Tool service](../features/tool-service.md)**
  (`flowgentic.tools.service`). One process calls MCP tools for many agent
  processes over queues, through a pool of MCP clients that keeps a failed
  connection from cancelling other calls.
- **[Key-value store](../features/checkpointer.md)** (`flowgentic.store`).
  Holds the suspended agents' state: RaDEX on an allocation, a dictionary in
  the process elsewhere.
- **[Fleet](../features/fleet.md)** (`flowgentic.fleet`). Sends each agent's
  model calls to one replica, so its prompt stays in that replica's prefix
  cache, and admits calls while the tokens of live context in flight stay under
  a cap. A replica is a transport: `HTTPTransport` for an OpenAI-compatible
  server, such as vLLM, SGLang or OpenRouter.

## A worked example

`examples/agents-at-scale/campaign.py` runs a whole campaign on a laptop. Each
agent looks its candidate up through an MCP tool, runs a simulation as a
RHAPSODY task and suspends while it runs, takes a note inline, and answers.
Everything around Flowgentic's parts stands in for an allocation: a scripted
model, as a transport, behind a fleet of two replicas, RHAPSODY on a thread
pool, an in-memory store, the tool service in a thread, and an MCP server in
a process of its own on 127.0.0.1.

```bash
uv run --extra agents python examples/agents-at-scale/campaign.py --agents 1000
uv run --extra agents python examples/agents-at-scale/campaign.py --agents 1000 --processes 2
```

With `--processes 2`, two agent processes each run a host on half the agents,
and both share the one tool service over `multiprocessing` queues, as agent
processes share it over Dragon's queues on an allocation. CI runs both forms
(`tests/integration/test_agents_at_scale.py`), from the wheel installed with
the `agents` extra and no LangGraph.

The tools: one of each kind. The inline one is a plain function.

```python
--8<-- "examples/agents-at-scale/campaign.py:tools"
```

Each agent process builds its parts. With `--dragon` (see
[Running under Dragon](#running-under-dragon)), the session runs on RHAPSODY's
native Dragon backend, the store is `RadexStore.attach(descriptor)` and the
queues are Dragon's; on an allocation each replica is an
`HTTPTransport(model, base_url=...)` of a model server:

```python
--8<-- "examples/agents-at-scale/campaign.py:process"
```

The runner: Flowgentic's loop over the fleet, the tools' declarations, the
runtime they run on, and the store.

```python
--8<-- "examples/agents-at-scale/campaign.py:runner"
```

And its host, which builds each agent's input as a slot opens, runs the
process's share of the agents, each starting a trace of its own (see
[Live dashboard](../features/live-dashboard.md#stream-a-run-to-it)), and then
stops the tool service client and closes the RHAPSODY session:

```python
--8<-- "examples/agents-at-scale/campaign.py:host"
```

The loop deletes an agent's state from the store when the agent ends,
however it ends, and tells the fleet that the agent makes no more model
calls. The store then holds the agents that are suspended, or running since
a suspension, not every agent of the campaign, which a Dragon dictionary
needs: its size is fixed when it is created.

`--json` prints the whole summary, with each process's `HostReport` figures,
the fleet's, the tools' and the loop's counters.

## Serving agents on demand

A campaign knows its agents up front. A server does not: an agent starts when
a message arrives, such as a request from another agent over a protocol like
A2A, and the server answers the message when the agent ends. A host started
once takes such agents one at a time, with everything `run` gives them but
the stagger: the cap, the collector's settings, suspension, the accounting
and `on_done`. It starts each agent as it comes, unless it is given a
stagger, as this example's host is.

With `--arrivals RATE`, the example's agents arrive one at a time, `RATE` a
second on average across the processes, and each process's host takes each
as it comes:

```bash
uv run --extra agents python examples/agents-at-scale/campaign.py --agents 1000 --arrivals 500
```

Each message gets a handler, which gives the host its agent and waits for the
agent's end:

```python
--8<-- "examples/agents-at-scale/campaign.py:on-demand"
```

- `async with host:` starts the host's period, with the collector's settings,
  the probes and the accounting, as `run` does, and stops the host at the
  block's end: cancelling its agents, if the block raised, so that an error
  never leaves the collector tuned.
- `submit(agent)` never blocks. It returns a future of `(result, output)`, what
  `on_done` receives; the agent starts as soon as a slot is free, after the
  agents submitted before it. An agent's error is in its result, never raised.
- With `max_queued`, `submit` raises `QueueFull` once that many agents wait
  for a slot, and the handler answers "busy, retry later" rather than pile
  the work up.
- `stop()` refuses new agents, except those the host's own agents submit,
  waits for the live and waiting ones, and returns the `HostReport`;
  `stop(drain=False)` cancels them instead.

An agent's id is free again once it has ended, so a server can run one
logical agent once per message under one id. The rest, cancelling one agent,
ids, contexts and the report, is on the
[Agent host](../features/agent-host.md#agents-on-demand) page. CI runs the
example with `--arrivals` in one and two processes, on the loop and on
LangGraph (`tests/integration/test_agents_at_scale.py`).

## Choosing the limits

The host's report says where the agents spent their time, and what running
them cost the process. Read it on a run of your own agents, at the scale of
one process, before choosing the limits.

These figures come from the example with 1,000 agents on a laptop (an Apple
M4 Max, 14 cores, 36 GB of memory, Python 3.11, with other work running).
Each column is one run of three, the one with the median time:

| | One agent process | Two agent processes |
| --- | --- | --- |
| Agents done | 1,000 of 1,000 | 1,000 of 1,000 |
| The hosts ran for | 1.7 s | 1.2 s |
| Agent-seconds working (`WORKING`) | 0.5 | 0.5 |
| in a tool call, the lookup (`TOOL_CALL`) | 628 | 501 |
| waiting for model admission (`MODEL_QUEUED`) | 0.02 | 5.2 |
| at the model (`MODEL_REQUEST`) | 20 | 21 |
| waiting for a task (`TOOL_WAIT`) | 13 | 15 |
| Share of the agents' lifetimes accounted for | 100.00 % | 100.00 % |
| Event loop lag, time-weighted p99 and maximum | 0.0 and 0.7 ms | 0.0 and 0.5 ms |
| Collector pauses, in all | 0.03 s | 0.07 s |

- **Agent-seconds by state** (`report.states`). Every agent is in one state at
  a time, so working, waiting and making model requests add up to the agents'
  lifetimes; in these runs, to within 0.0001 %. The agents spent most of their
  time in `TOOL_CALL`, waiting for the lookup: the one tool service, in a
  thread of the first process, served a thousand agents through two MCP
  clients. Their own work (`WORKING`) took half an agent-second in all. Read
  the states to find what to move, or add, first: here, the tool service's
  clients.
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
  with `Fleet(process=(index, processes))` each process holds its exact part
  of them (see [Fleet](../features/fleet.md#many-agent-processes)). Set
  `tokens` to what the server's KV cache holds, and watch `MODEL_QUEUED`
  against `MODEL_REQUEST`: agents queued while the server is not full mean a
  cap that is too low.
- **Task workers.** Agents in `TOOL_WAIT` wait for their tasks. If they
  wait longer than the tasks run, the session's backend needs more workers.

## Running under Dragon

On an allocation the application runs under Dragon, and creates the same parts
on Dragon's resources:

- **The dictionary.** RHAPSODY's `DragonDataBackend` creates a Dragon
  distributed dictionary; its endpoint's `serialize()` gives the descriptor.
  Each agent process attaches once, with `RadexStore.attach(descriptor)`, and
  passes the store to its `LoopRunner`, or to LangGraph's `KeyValueSaver`.
- **The tasks.** Each agent process creates a RHAPSODY `Session` on a Dragon
  execution backend, and passes it to its `ToolRuntime`.
- **The tool service.** One Dragon process runs `serve(requests, replies,
  pool)` on Dragon queues: one request queue, and one reply queue per agent
  process. Each agent process starts its `ToolServiceClient` on its own reply
  queue.
- **The agent processes.** Each runs one host on a disjoint range of agents
  (`run_indexed`), or takes its agents as they arrive (`start` and
  `submit`), and a fleet with `process=(index, processes)` over the model
  servers' replicas: with `layout="pack"`, each process sends to a few of
  them.
- **The model servers** run on the allocation's GPUs, and each replica is an
  `HTTPTransport` of one server.

Flowgentic imports nothing from Dragon: the application passes in the queues,
the session and the store.

The example does all of this with `--dragon`, under `dragon`. Only its model
stays scripted:

```bash
dragon examples/agents-at-scale/campaign.py --dragon --agents 1000 --processes 2
```

It needs Dragon, RaDEX's Python client, and a RHAPSODY with the native Dragon
backend, `DragonNativeExecutionBackend`. RHAPSODY sends a task's function to
its workers with cloudpickle, which sends a function by name when its module
looks importable; the workers cannot import the example, so the example has
cloudpickle send its functions by value. An application whose task functions
live in an installed package needs nothing of the kind. The example also sets
`DRAGON_PATCH_MP` again after importing RHAPSODY's backends: the import loads
Dragon's LLM engine, which clears it, and processes started through Dragon's
`multiprocessing` need it
([radical-cybertools/rhapsody#159](https://github.com/radical-cybertools/rhapsody/issues/159)).

CI runs the example without Dragon. For 0.7.0 the parts were tested under
Dragon by hand: the store's contract on RaDEX, an agent suspended by one
Dragon process and resumed by another, the tool service in a Dragon process
serving two agent processes over Dragon's queues, and the worked example
adapted to Dragon, with 1,000 agents in two processes, on the loop and with
`--runner graph`, every suspension resumed from RaDEX, and with 200 agents
arriving one at a time (`--arrivals`). They passed under
Dragon 0.15.0 on Python 3.12, with RHAPSODY at commit `9e103fd`, whose native
Dragon backend is not in a release yet, and RaDEX at commit `9f9b0df`.
See [Project status](../status.md#known-limitations).

## With LangGraph

With the `langgraph` extra, the same agents run as a LangGraph graph, in the
same host, on the same tools, fleet and store:

```bash
uv sync --locked --extra agents --extra langgraph
uv run --extra agents --extra langgraph python examples/agents-at-scale/campaign.py --agents 1000 --runner graph
```

`examples/agents-at-scale/campaign_langgraph.py` holds the graph. It calls the
fleet directly from its model node, with the agent's id, and sends each tool
call to the tool node as its own task, with
`flowgentic.langgraph.route_tool_calls`, so a resumed agent runs no other call
again:

```python
--8<-- "examples/agents-at-scale/campaign_langgraph.py:graph"
```

Its runner: `bind_tools` turns the declarations into LangGraph's tools, and
`KeyValueSaver` keeps the graph's checkpoints in the store
([Checkpointer](../features/checkpointer.md)). With `GraphRunner`'s `exit`
durability an agent writes a checkpoint when it suspends and when it ends;
`delete_finished=True` deletes each agent's thread once the agent finishes,
as the loop deletes its state; see the
[agent host](../features/agent-host.md#runners) for what the option gives up.

```python
--8<-- "examples/agents-at-scale/campaign_langgraph.py:runner"
```

Its replicas are LangChain chat models, which the fleet runs through
LangChain's adapter (`flowgentic.langchain`); `HTTPTransport` replicas work
too, through `fleet.chat_model()`. [Agent loop](../features/agent-loop.md)
compares the two runners, and measures them on this example's workload. An
application that moves its agents from the graph to the loop sees two
differences: a task tool's `suspend` is asked before the task is submitted,
not after `prepare`, and the prompts' lengths can differ by a few tokens per
call, because the loop renders the tools' schemas through pydantic and
LangChain with its own converter ([Compared with
`GraphRunner`](../features/agent-loop.md#compared-with-graphrunner)).

## Telemetry

Each part records OpenTelemetry spans and metrics through the API; the
application configures the SDK and its exporters, as
[Telemetry](../features/telemetry.md) describes. Each agent's run is an
`invoke_agent` span, each task or service call an `execute_tool` span, and
the agents by state, the host's loop lag and collector pauses, the fleet's
admission and the tools' durations are metrics. Each feature page lists its
own.
