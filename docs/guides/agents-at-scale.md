# LangGraph agents at scale

This guide is for campaigns of many LangGraph agents, thousands to hundreds of
thousands, on an HPC allocation. Each agent's tools run as HPC tasks, its model
is served on the allocation, and its state lives in the allocation's memory.
Install the path's extras:

```bash
uv pip install "flowgentic[langgraph,rhapsody,mcp]"
```

RaDEX, which keeps agents' state in Dragon's distributed dictionary, is built
from source with its C++ core: see
[radical-cybertools/radex](https://github.com/radical-cybertools/radex).

## The agent process

An agent process runs one `AgentHost`, which runs up to `max_live_agents`
agents of one graph at once:

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

The application starts everything else: the agent processes, the model servers,
the dictionary, and the tool service. On Dragon these are Dragon processes, and
the tool service's queues are Dragon queues.

## The parts

**`flowgentic.host.AgentHost`** runs agents through a framework's runner,
`flowgentic.langgraph.GraphRunner` for LangGraph. While it runs, it raises the
cyclic collector's first threshold and freezes the objects alive once agents
have started: with CPython's defaults, a process with thousands of agents
spends most of its time scanning their objects. It restores the collector when
it returns. It keeps at most `max_live_agents` agents live, suspended ones
included, and starts them in staggered batches so they do not all reach their
first model call or suspension at once. One agent's failure never stops the
others. Its report gives each agent's outcome, the agent-seconds spent working,
waiting and making model requests, the event loop's lag, and the collector's
pauses. Read the loop lag to choose `max_live_agents` for your agents.

**`flowgentic.tools`** declares where each tool runs. A LangChain tool runs
inline. `task_tool(fn)` runs `fn` as a RHAPSODY function task in the process's
session, on whichever backend the application created. With `suspend=True`,
the agent's state is saved and dropped while the task runs, and the host
resumes it when the task ends. `mcp_tool(name, ...)` calls a tool on an MCP
server through the shared tool service. Task and MCP calls are keyed by the
agent and the call's id, so a call made again, as LangGraph does when it
resumes an agent, gets the first call's result and runs nothing.
`flowgentic.langgraph.bind_tools(runtime, tools)` turns the declarations into
LangChain tools.

**`flowgentic.langgraph.route_tool_calls`** sends each tool call to the tool
node as its own task. LangGraph then saves each finished call's result with the
checkpoint, so when an agent resumes from a suspension, only the call that
suspended runs again. Use it as the model node's conditional edge.

**`flowgentic.langgraph.KeyValueSaver`** is a LangGraph checkpointer over a
key-value store: `RadexStore.attach(descriptor)` on an allocation, `MemoryStore()`
elsewhere. It keeps the newest checkpoint of each thread. Run graphs with
`exit` durability, `GraphRunner`'s default, and an agent writes a checkpoint
only when it suspends and when it ends.

**`flowgentic.fleet.Fleet`** sends each agent's calls to model server replicas.
`AgentAffinity` routing keeps an agent on one replica, so its growing prompt
stays in that replica's prefix cache. `TokenAdmission` admits calls while the
tokens of live context in flight stay under a cap: past the point where a
server's cache holds its agents' contexts, every call recomputes its history.
Each agent process takes `share` of every replica's caps. With `Recovery`, a
call whose server went away waits for it to come back.

**`flowgentic.tools.service`** is the tool service: `serve(...)` in its own
process, a `ToolServiceClient` in each agent process. Its MCP clients form a
`SupervisedMcpPool`, which keeps a failed connection from cancelling anything
but that connection's calls in flight.

## A worked example

`examples/agents-at-scale/campaign.py` runs a whole campaign in one process:
a scripted fleet, RHAPSODY on a thread pool, an in-memory store, and the tool
service in a thread with an MCP server on 127.0.0.1. Each agent looks its
candidate up through the MCP tool, simulates it as a task and suspends while
the task runs, takes a note inline, and answers.

```bash
uv run --extra dev python examples/agents-at-scale/campaign.py --agents 1000
```

## Telemetry

Flowgentic records OpenTelemetry metrics and spans through the API only; the
application configures the SDK and the exporters. The metrics:

| Metric | What |
| --- | --- |
| `flowgentic.agents` | Agents in each state, read when the SDK collects |
| `flowgentic.agent.time` | Agent-seconds in each state, with its category: working, waiting, making a model request |
| `flowgentic.host.loop.lag` | How late the host's event loop woke a 100 ms timer |
| `flowgentic.host.gc.pause` | The cyclic collector's pauses, by generation |
| `flowgentic.fleet.admission.wait` | A model call's wait for admission, by replica |
| `flowgentic.fleet.request.duration` | A model call's time at its replica |
| `flowgentic.fleet.tokens.active`, `.requests.active`, `.requests.waiting` | Each replica's admission state |
| `flowgentic.tool.duration` | A task or service call's time to its result, by tool |

Each agent's run is an `invoke_agent` span, with a `flowgentic.agent.suspend`
event per suspension. Tasks run in the trace context of the tool that submitted
them.
