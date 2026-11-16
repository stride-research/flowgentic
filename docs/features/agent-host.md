# Agent host

A campaign of agents on an HPC allocation runs thousands of them per Python
process. `flowgentic.host.AgentHost` runs them: it starts them, keeps the
process responsive while they run, resumes the ones that wait on tasks, and
counts what each is doing. It works with any agent framework through a small
`Runner`; `flowgentic.langgraph.GraphRunner` is LangGraph's.

```python
from flowgentic.host import Agent, AgentHost
from flowgentic.langgraph import GraphRunner

host = AgentHost(GraphRunner(graph))   # graph: a compiled LangGraph graph
report = await host.run(
    Agent(f"agent-{i}", {"messages": [("human", task)]}) for i, task in enumerate(tasks)
)
print(report.finished, report.states["share_by_category"], report.loop_lag)
```

`flowgentic.host` needs no extra. `GraphRunner` needs the `langgraph` extra.

## What the host does

- **It tunes the cyclic collector while it runs.** At CPython's defaults the
  collector runs every few hundred allocations and scans every young object.
  With thousands of agents in a process, each holding its conversation, that
  scanning takes most of the process's time. The host raises the collector's
  first threshold (`Collector(threshold=100_000)`) and, once agents have
  started, moves the objects then alive out of the collector's reach
  (`gc.freeze()`). It restores both when `run` returns, and also when `run` is
  cancelled. `collector=None` leaves the collector alone.
- **It caps the agents live at once.** At most `max_live_agents` (5,000 by
  default) are live, suspended ones included. More are accepted, and each
  starts when a live one ends. The cap bounds how late the event loop runs and
  how long the collector pauses. Measure both for your own agents with
  `HostReport.loop_lag` and `HostReport.collector`, and set the cap from them.
- **It staggers starts.** Agents start `Stagger(batch=100, interval_s=0.5)` at
  a time, so they do not all reach their first model call, or their first
  suspension, at the same moment.
- **It resumes agents that wait on tasks.** A runner reports a suspended agent
  with the keys of the tasks it waits on. The host holds only the agent's id
  and those tasks' futures, awaits the tasks, and resumes the agent; the
  framework keeps the agent's state in its checkpoint meanwhile.
- **It isolates failures.** One agent's exception ends that agent, with the
  error in its result, and never the others.
- **It keeps outputs out of memory.** Each agent's output goes to `on_done`,
  if given, and is not kept: a finished agent's state can be large.

## What it reports

`run` returns a `HostReport`: each agent's status (`done`, `failed` or
`interrupted`), error, times and suspensions; the agent-seconds spent in each
state; the event loop's lag (time-weighted p99, maximum, share of time over
100 ms); and the collector's pauses by generation.

`AgentStates` counts agents in seven states and three categories, the ones a
campaign reports:

| State | Category | The agent is |
| --- | --- | --- |
| `WORKING` | working | in the agent process: the framework's steps, inline tools |
| `TOOL_CALL` | working | waiting for a call it made, such as an MCP tool |
| `MODEL_QUEUED` | waiting | waiting to be admitted to a model server |
| `MODEL_REQUEST` | making a model request | waiting for a model server's answer |
| `TOOL_WAIT` | waiting | waiting for a task, queued or running, suspended or not |
| `PENDING`, `DONE` | | not started, or ended |

The counts can be read from another thread while agents run, for a sampler of
your own: pass `states=` to share one `AgentStates`.

## Runners

A `Runner` has two methods: `start(agent)` runs the agent until it ends,
suspends or stops; `resume(agent, done)` resumes it after its tasks have
ended. Each returns `Finished(output)`, `Suspended(waits)` or
`Interrupted(interrupts)`. `waits` pairs a resume id with a task key, and each
key names a future registered in the process with `register_pending`.

`GraphRunner(graph)` runs each agent as one thread of a compiled graph, with
the agent's id as `thread_id` and LangGraph's `exit` durability by default, so
a checkpoint is saved only when a run ends or suspends. A tool suspends its
agent by calling LangGraph's `interrupt({PENDING_KEY: key})` for a task
registered under `key`, and records the interrupt with `record_interrupt`, so
the runner frees the run's frames as soon as it stops. Any other interrupt,
such as a question for a human, ends the agent as `interrupted`.

`route_tool_calls` is the conditional edge after the model node: it sends
each tool call to the tool node as its own task (LangGraph's `Send`).
LangGraph saves each finished call's result with the checkpoint, so when an
agent resumes, only the call that suspended runs again.

```python
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from flowgentic.langgraph import route_tool_calls

builder = StateGraph(MessagesState)
builder.add_node("model", call_model)
builder.add_node("tools", ToolNode(tools))
builder.add_edge(START, "model")
builder.add_conditional_edges("model", route_tool_calls, ["tools", "__end__"])
builder.add_edge("tools", "model")
graph = builder.compile(checkpointer=checkpointer)
```

## Telemetry

Through the OpenTelemetry API, and only once the application configures an
SDK:

- each agent's run is an `invoke_agent {agent id}` span, with
  `gen_ai.agent.id`, and a `flowgentic.agent.suspend` event per suspension;
- `flowgentic.agents` counts agents by `flowgentic.agent.state`, and
  `flowgentic.agent.time` adds up agent-seconds by state and
  `flowgentic.agent.category`, both read when the SDK collects;
- `flowgentic.host.loop.lag` and `flowgentic.host.gc.pause` record the event
  loop's lag and the collector's pauses.
