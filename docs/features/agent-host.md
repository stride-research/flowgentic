# Agent host

A campaign of agents on an HPC allocation runs thousands of them per Python
process. `flowgentic.host.AgentHost` runs them: it starts them, keeps the
process responsive while they run, resumes the ones that wait on tasks, and
counts what each is doing. It works with any agent framework through a small
`Runner`; `flowgentic.langgraph.GraphRunner` is LangGraph's, and
`flowgentic.loop.LoopRunner` is Flowgentic's own [agent loop](agent-loop.md),
on OpenAI-format messages, with no LangChain.

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
  first threshold (`Collector(threshold=100_000)`; it must be at least 1,
  since 0 would turn automatic collection off) and, once agents have
  started, moves the objects then alive out of the collector's reach
  (`gc.freeze()`). It restores both when `run` returns, and also when `run` is
  cancelled. Overlapping runs share the collector tuning until the last user
  releases it. When the last host that froze ends, it unfreezes every frozen
  object, those frozen before it included (CPython 3.12 parks some of its own
  objects there): CPython cannot unfreeze only part, and reference cycles
  left frozen would never be freed. An application that keeps objects of its
  own frozen passes `Collector(freeze=False)`; `collector=None` leaves the
  collector alone.
- **It caps the agents live at once.** At most `max_live_agents` (5,000 by
  default) are live, suspended ones included. More are accepted, and each
  starts as soon as a live one ends. The cap bounds how late the event loop
  runs and how long the collector pauses. Measure both for your own agents
  with `HostReport.loop_lag` and `HostReport.collector`, and set the cap from
  them.
- **It staggers the first wave.** The first `max_live_agents` agents start
  `Stagger(batch=100, interval_s=0.5)` at a time, so they do not all reach
  their first model call, or their first suspension, at the same moment. Later
  agents are not staggered: their starts follow the ends of live agents.
- **It resumes agents that wait on tasks.** A runner reports a suspended agent
  with the keys of the tasks it waits on. The host holds only the agent's id
  and those tasks' futures, awaits the tasks, and resumes the agent; the
  framework keeps the agent's state in its checkpoint meanwhile.
- **It isolates failures.** One agent's exception ends that agent, with the
  error in its result, and never the others. An exception from `on_done` makes
  the agent `failed` and is kept in `callback_error`, beside the agent's own
  `error`.
- **It stops cleanly when cancelled.** When `run` is cancelled, each live agent
  ends as `cancelled`, not `failed`; `on_done` receives it, and `run` raises
  `CancelledError`. Agents that had not started stay `PENDING`.
- **It keeps outputs out of memory.** Each agent's output goes to `on_done`,
  if given, and is not kept: a finished agent's state can be large.

## What it reports

`run` returns a `HostReport`: each agent's status (`done`, `failed` or
`interrupted`), error, times and suspensions; the count of each status
(`outcomes`); the agent-seconds spent in each state; the event loop's lag
(time-weighted p99, maximum, share of time over 100 ms); and the collector's
pauses by generation. Its `collector["freeze"]` gives when the host froze
(`at_s`), how long the freeze took (`call_s`), and the objects frozen before
(`frozen_before`) and after (`frozen`).

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

Each run requires unique agent IDs and starts its own accounting period.
The counts can be read from another thread while agents run, for a sampler of
your own: pass your `AgentStates` as `states=` and read it while the host runs.
An `AgentStates` tracks one run at a time, so hosts that run at the same time
each need their own; a second run on one that is in use raises `RuntimeError`.

## Runners

A `Runner` has two methods: `start(agent)` runs the agent until it ends,
suspends or stops; `resume(agent, done)` resumes it after its tasks have
ended. Each returns `Finished(output)`, `Suspended(waits)` or
`Interrupted(interrupts)`. `waits` pairs a resume id with a task key, and each
key names a future registered in the process with `register_pending`: the
agent's own registration first, so that two hosts in one process can run
agents with the same ids. A runner that suspends an agent 100 times in a row
on the same tasks, all already ended, makes no progress: the host fails the
agent with a `RuntimeError`, rather than resume it at once for ever.

`LoopRunner(model, tools)` runs a tool-calling agent as a loop, with no graph,
and saves a suspended agent's messages to a key-value store; see
[Agent loop](agent-loop.md). The rest of this section is about `GraphRunner`.

`GraphRunner(graph)` runs each agent as one thread of a compiled graph, with
the agent's id as `thread_id` and LangGraph's `exit` durability by default, so
a checkpoint is saved only when a run ends or suspends. A tool suspends its
agent by calling LangGraph's `interrupt({PENDING_KEY: key})` for a task
registered under `key`. The host releases an agent’s registrations when it
finishes. A future registered outside an agent's task, as by code that
submits tasks before a run, becomes the agent's that first waits on it, and
is released when that agent finishes. One that no agent takes is released
when the run ends, or the last of the runs in progress if several overlap;
one registered when no run follows stays registered for the life of the
process. The tool records the interrupt with `record_interrupt`, so
the runner frees the run's frames as soon as it stops. Any other interrupt,
such as a question for a human, ends the agent as `interrupted`.

A finished agent's thread stays in the graph's checkpointer, so the store
grows with the campaign. With `GraphRunner(graph, delete_finished=True)`, the
runner deletes the thread when the agent finishes, before the host passes the
output to `on_done`. The store then holds the agents that are running, not
every agent of the campaign: turn it on when the store is a Dragon dictionary,
whose size is fixed when it is created. An interrupted agent keeps its thread
even then, so it can resume once a human answers, and so does a failed or
cancelled one, to debug or retry it. If a thread cannot be deleted, the runner
logs it and counts it in `delete_failures`, and the agent still finishes.

Turning it on gives up two things. `on_done` still gets the agent's output,
but the thread's state is gone by then: `graph.get_state` there finds an empty
thread. And an agent id run again, as a conversation continued in a later host
run, starts a fresh thread, without the earlier turns.

Each resume is a new LangGraph run, which counts its steps against
`recursion_limit` afresh. So `GraphRunner` also lets one agent resume at most
`recursion_limit` times, and fails it with `GraphRecursionError` after that.
Each resume runs at least one step, so an agent that would finish within the
limit without suspending is never stopped by this. The count lasts until the
agent's run ends, or until the host ends the agent, as when it cancels or fails
an agent that waits between two resumes. `GraphRunner` refuses a
graph compiled with `interrupt_before` or `interrupt_after`: a static
breakpoint stops a run without an interrupt, and the agent would end as
finished, with partial output.

`route_tool_calls` is the conditional edge after the model node: it sends
each tool call of the last message in the state's `messages` to the node named
`tools`, each as its own task (LangGraph's `Send`).
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

### A runner of your own

A runner of the application's own, such as one whose code, not a model, picks
each step, uses what Flowgentic's runners use, from `flowgentic.host`:

- `current_agent()`: the id of the agent the current coroutine works for, or
  `None` outside the host's agents. The host sets it in each agent's task, and
  asyncio copies it into the tasks and worker threads a runner starts for the
  agent, so an inline tool reads its agent here too, under `LoopRunner` and
  `GraphRunner`.
- `activity(state)`: counts the current agent in `state` for a block:
  `TOOL_CALL`, `MODEL_REQUEST`, `MODEL_QUEUED` or `TOOL_WAIT`. An agent with
  no activity open is `WORKING`, and the host alone makes it `PENDING` or
  `DONE`, so any other state is a `ValueError`. Activities nest and overlap;
  the agent's state is the most telling of those open, in that order. A
  fleet counts its own calls.
- `wait(futures)`: waits for tasks, such as a `ToolRuntime`'s, without raising
  their errors. A task whose order has not admitted it yet is withdrawn, and
  never starts, once every wait on it is cancelled, as when the host stops
  its agent.
- `register_pending`, above, for a task the runner suspends its agent on;
  `ToolRuntime.submit` registers its tasks itself.

```python
from flowgentic.host import AgentState, Finished, activity, wait


class Scripted:
    """Each agent runs one simulation, with arguments its code chooses."""

    async def start(self, agent):
        job = runtime.submit(simulate, agent.id, "sim0", {"x": choose(agent.input)})
        with activity(AgentState.TOOL_WAIT):
            await wait((job,))
        return Finished(job.result())

    async def resume(self, agent, done): ...
```

## Telemetry

Through the OpenTelemetry API, and only once the application configures an
SDK:

- each agent's run is an `invoke_agent` span, with the agent's id in
  `gen_ai.agent.id`, and a `flowgentic.agent.suspend` event per suspension; a
  failed agent's span has status ERROR, `error.type` and the exception;
- `flowgentic.agents` counts the agents of runs in progress by
  `flowgentic.agent.state`, and the counter `flowgentic.agent.time` adds up
  agent-seconds by state and `flowgentic.agent.category` over every run in
  the process, both read when the SDK collects;
- `flowgentic.host.loop.lag` and `flowgentic.host.gc.pause` record the event
  loop's lag and the collector's pauses, once each however many hosts run.

[Telemetry](telemetry.md#metrics) lists every metric, with its unit and
buckets, and shows how to configure a meter provider.

## Campaigns with millions of agent IDs

Use `run_indexed` when agents can be named by a prefix and an integer. The
host builds each input as a slot opens, keeps detailed state for live agents
only, and returns counts in `report.outcomes` but no per-agent results in
`report.agents`. Its `on_done` callback receives
each individual result. Keep that callback bounded too. The ordinary `run`
keeps its full per-agent report and validates all application-supplied IDs
before starting work.

```python
host = AgentHost(runner, max_live_agents=256, on_done=save_result)
report = await host.run_indexed(range(start, stop), make_input, id_prefix="campaign-")
```

Give different processes disjoint index ranges, so that no agent runs in two
processes. Both entry points run agents through the same runner, and the
runner keeps each agent's state as it does in `run`. The total campaign
population and the number of agents live at once are separate sizing
parameters: each live agent can hold a framework run, pending futures and
external requests, so set `max_live_agents` from what one process sustains.

To measure host scheduling and peak process RSS on Linux or macOS, run
`uv run --locked python benchmarks/agent_host.py --agents 100000`.
Add `--full-report` for the ordinary host path. The runner finishes immediately,
uses generated inputs and performs no model, checkpoint or network calls.
