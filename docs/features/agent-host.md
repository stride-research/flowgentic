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

`run` takes a fixed set of agents and returns when every one has ended. A
host can also take agents one at a time, as they arrive, between `start` and
`stop`: see [Agents on demand](#agents-on-demand).

## What the host does

- **It tunes the cyclic collector while it runs.** At CPython's defaults the
  collector runs every few hundred allocations and scans every young object.
  With thousands of agents in a process, each holding its conversation, that
  scanning takes most of the process's time. The host raises the collector's
  first threshold (`Collector(threshold=100_000)`; it must be at least 1,
  since 0 would turn automatic collection off) and moves the objects alive out
  of the collector's reach (`gc.freeze()`): `run` once its first wave of
  agents has started, and a started host in `start()`, before its first agent
  ([Agents on demand](#agents-on-demand)). It restores both when `run` returns
  or `stop` ends a started host, and also when either is cancelled.
  Overlapping runs share the collector tuning until the last user releases it,
  and the first to freeze freezes for them all: a `run` that starts while a
  started host serves freezes nothing of its own. When the last host that
  froze ends, it unfreezes every frozen object, those frozen before it
  included (CPython 3.12 parks some of its own objects there): CPython cannot
  unfreeze only part, and reference cycles left frozen would never be freed.
  An application that keeps objects of its own frozen passes
  `Collector(freeze=False)`; `collector=None` leaves the collector alone.
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
  `CancelledError`. Agents that had not started stay `PENDING`. A started
  host's `stop(drain=False)` ends its live agents the same way.
- **It keeps outputs out of memory.** Each agent's output goes to `on_done`,
  if given, and is not kept: a finished agent's state can be large.

## What it reports

`run` returns a `HostReport`: each agent's status (`done`, `failed` or
`interrupted`), error, times and suspensions; the count of each status
(`outcomes`); the agent-seconds spent in each state; the event loop's lag
(time-weighted p99, maximum, share of time over 100 ms); and the collector's
pauses by generation. Its `collector["freeze"]` gives when the host froze
(`at_s`, 0 for a started host), how long the freeze took (`call_s`), and the
objects frozen before (`frozen_before`) and after (`frozen`).

The host keeps running values of the lag and the pauses, not every sample, so
their memory stays the same however long it runs. Every figure is exact but
the lag's time-weighted p99, which comes from a histogram of fixed size
(buckets 1 % wide from 0.1 ms up): it is within 1 % of the exact value, or 0.1
ms, whichever is larger, for a p99 under a day, before the report rounds it to
0.1 ms.

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

Each run requires unique agent IDs and starts its own accounting period; so
does each period of a started host, from `start` to `stop`.
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
finishes. A future registered outside an agent's task, as by code that submits
tasks before a run, becomes the agent's that first waits on it, and is
released when that agent finishes. One that no agent takes is released when
the run ends, or the last of the runs in progress if several overlap (a
started host's period counts as a run, until `stop`); one registered when no
run follows stays registered for the life of the process. The tool records the
interrupt with `record_interrupt`, so the runner frees the run's frames as
soon as it stops. Any other interrupt, such as a question for a human, ends
the agent as `interrupted`.

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
  the tasks started from there inherit it. A worker thread sees it only when
  started with `asyncio.to_thread` or in a copied context
  (`contextvars.copy_context().run`), as `LoopRunner` and `GraphRunner` run
  sync inline tools, so an inline tool reads its agent here under both.
  `loop.run_in_executor` alone does not carry it.
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
Add `--full-report` for the ordinary host path, or `--submit` for agents given
one at a time to a started host. The runner finishes immediately, uses
generated inputs and performs no model, checkpoint or network calls. The
benchmark's host runs with `collector=None`, so its CPU time follows where
CPython's full collections fall; it prints the collector's passes in each
generation, and an A/B should compare runs with the same number of full
passes, or try more than one `--live`.

## Agents on demand

A server's agents do not come as a list: each starts when a message arrives,
and the server answers the message when its agent ends. A started host takes
them one at a time, with everything `run` gives: the cap, staggered starts,
the collector's settings, suspension, the accounting and `on_done`.

```python
from flowgentic.host import Agent, AgentHost, QueueFull, Stagger

host = AgentHost(
    runner,
    max_live_agents=5_000,
    max_queued=20_000,
    stagger=Stagger(batch=1, interval_s=0),  # start each agent as it comes
)


async def handle(message):  # one per message, on the host's event loop
    try:
        future = host.submit(Agent(f"{message.agent}:{message.task}", message.input))
    except QueueFull:
        return busy()  # "busy, retry later"
    result, output = await future  # what on_done receives
    return answer(output) if result.status == "done" else failed(result)


async with host:  # start(); on an error, stop(drain=False)
    await serve(handle)  # until the server shuts down
    report = await host.stop()  # waits for the live and waiting agents
```

- **`start()`** tunes the collector, starts the probes of the event loop and
  the collector, and begins an accounting period, as `run` does. It also
  freezes the objects alive then, after the full collection it runs anyway,
  so that no request waits for the freeze ("The first wave", below). A host
  runs one period at a time: `start`, `run` or `run_indexed` while it runs
  raises `RuntimeError`. After `stop`, it can start again, or run a list.
- **`async with host:`** starts the host, and stops it when the block ends:
  `stop()` if the block ended normally, `stop(drain=False)` if it raised.
  Until `stop`, the host keeps the collector's threshold raised, its objects
  frozen and its probes running, and refuses a second `start`, so start it
  with `async with`, or stop it in a `finally`. Inside the block, `await
  host.stop()` returns the report, and the block's end has nothing left to
  stop.
- **`submit(agent)`** never blocks. It returns an `asyncio.Future` that
  resolves to `(result, output)`, what `on_done` receives, when the agent
  ends: `done`, `failed`, `interrupted` or `cancelled`. An agent's error is in
  `result.error`, never raised by the future; an `on_done` that raises makes
  the result `failed`, with `callback_error`. The agent starts as soon as a
  slot is free, after the agents submitted before it. It runs in a copy of
  the caller's context, as a task `asyncio.create_task` makes there: its
  `invoke_agent` span is a child of the span current at `submit`, such as a
  server's request span. Call `submit` on the host's event loop; from another
  thread, use `asyncio.run_coroutine_threadsafe` with a coroutine that calls
  it.
- **Ids.** An agent's id must be unique among the host's live and waiting
  agents: `submit` raises `ValueError` otherwise. Once an agent ends, its id
  is free again, by the time its future resolves, so a server can run one
  logical agent once per message under one id. `LoopRunner` keys each run's
  tool calls apart, and `GraphRunner` continues the agent's thread, or starts
  a fresh one with `delete_finished=True`. An id may hold any character:
  task keys write a `/` in it as `%2F` ([Repeat
  safety](tools.md#repeat-safety)).
- **`max_queued`** bounds the agents that wait for a slot: `submit` raises
  `QueueFull` when the host holds `max_live_agents + max_queued` agents, live
  or waiting, so a server can refuse work rather than pile it up. With
  `max_queued=0`, an agent is taken only into a free slot. `None`, the
  default, accepts every agent. It does not bound `run` or `run_indexed`,
  which take their agents as slots free. With `None`, a burst can queue more
  agents than the host starts before their callers give up, and a queued agent
  whose caller timed out still runs when its turn comes, for no one, unless
  its future was cancelled. `asyncio.wait_for` and `asyncio.timeout` cancel
  it, so the agent never starts; `asyncio.wait` with a timeout,
  `asyncio.shield`, or a server that drops a request without cancelling its
  handler leave it queued. To bound that, set `max_queued` to about the agents
  the host starts within a caller's timeout, so that `submit` refuses the rest
  with `QueueFull`, and cancel the future when the caller gives up.
- **Suspended agents keep their slots.** A suspended agent is live, as in
  `run`: while all slots hold agents, live or suspended, new agents wait, and
  each starts when an agent ends.
- **The first wave.** The first `max_live_agents` agents the host starts are
  staggered, as in `run`, even when slots are free: with the default
  `Stagger(batch=100, interval_s=0.5)`, the last of a burst of 1,000 agents
  starts about 4.5 s after the first. A server whose messages come spread out
  gains nothing from it: `Stagger(batch=1, interval_s=0)` starts each agent as
  soon as a slot is free. A started host does not freeze after its first wave,
  as `run` does, but in `start()`: a freeze needs a full collection first,
  which would run on the event loop inside the first agent's request, and
  `start()` runs one anyway. With the agents-at-scale example's agent process
  on CPython 3.12, the freeze held the event loop inside the first request for
  46 ms with `LoopRunner` and 74 ms with `GraphRunner` (its `call_s`); in
  `start()` it adds a few microseconds. What the first agents create and keep,
  such as a framework's lazily built caches, is not frozen: about a thousand
  objects with `GraphRunner` and a few with `LoopRunner` in that process, and
  a full collection there took 0.1 ms either way. The raised threshold holds
  for every agent.
- **Cancelling one agent.** Cancelling a future cancels its agent: one still
  waiting never starts, and one live ends as `cancelled`, which `on_done`
  receives. A handler that awaits the future and is itself cancelled, as when
  a client goes away, cancels the future too, as `await` does; to let the
  agent run on, await `asyncio.shield(future)`. The future is cancelled at
  once, but the host learns of it one loop turn later: a waiting agent's id
  and its place under `max_queued` are free after an `await
  asyncio.sleep(0)`, and a live agent's once the agent has ended as
  `cancelled`, which its future, cancelled already, does not tell. An agent
  cancelled after the host took it but before its first step, by a cancel or
  by `stop(drain=False)`, never runs: it ends as `cancelled`, and `on_done`
  does not receive it, as for a waiting agent.
- **`stop(drain=True)`** refuses new agents (`submit` raises `RuntimeError`),
  except from the host's own agents, waits for the live and waiting agents to
  end, ends the host's period, and returns its `HostReport`.
  **`stop(drain=False)`** cancels the live agents, which end as `cancelled`,
  and withdraws the waiting ones, which never start: each of their futures
  resolves to a `cancelled` result whose `t_start` is `0.0`, and `on_done`
  does not receive them. `stop(drain=False)` while a `stop()` drains cancels
  what is left, and both return the same report, so a shutdown with a deadline
  reads:

    ```python
    stopping = asyncio.create_task(host.stop())
    done, _ = await asyncio.wait({stopping}, timeout=30)
    if not done:
        await host.stop(drain=False)  # cancel what is left
    report = await stopping
    ```

    Cancelling `stop` ends the host as cancelling `run` does. `stop` ends what
    `start` began: while `run` or `run_indexed` runs, it raises
    `RuntimeError`, and cancelling `run` ends a run early.
- **The report** keeps aggregate counts, as `run_indexed`'s does: `agents` is
  empty, since each result went to its future and to `on_done`, and
  `outcomes` counts every agent submitted once, those withdrawn before they
  started as `cancelled`. While the host runs, `host.states` counts the
  waiting agents as `PENDING`, and keeps no state of an agent that has ended.
- **Agents that call agents.** An agent may submit another to its own host
  and await it, as an agent that calls another in the process. Keep
  `max_live_agents` above the number of agents that can wait on others at
  once: an agent waiting on one that cannot start holds its slot. While
  `stop()` drains, the host still takes the agents its own agents submit,
  from their tasks, the tasks they start and `on_done`, so a chain of agents
  ends as it would have; `stop(drain=False)` cancels the whole chain. An agent
  cannot stop the host it runs in, nor can a task it starts, while the agent
  runs: `stop` raises `RuntimeError` there, since it would wait for the
  agent's end.
- **The host's own task.** The host starts its agents from a task of its own.
  If that task is cancelled from outside, as by code that cancels every task,
  the host starts no more agents: it withdraws the waiting ones, whose futures
  resolve to `cancelled` results, refuses new ones, and logs a warning. The
  live agents run to their ends, and `stop` still ends the host and returns
  its report.

A host that serves for days keeps, until `stop`, the task registrations made
outside an agent that no agent took (`register_pending`, above). Its probes
of the event loop and the collector keep running values and a histogram of
fixed size, about 33 KB, however long it serves. With `GraphRunner`, the
checkpointer keeps each finished agent's last checkpoint, past `stop` too,
unless `delete_finished=True`.
