# Tool placement

An agent's tools do different kinds of work. A note or a parser takes
microseconds; a simulation takes minutes and wants a core of its own; a
database lookup lives behind an MCP server. Flowgentic lets each tool say where
it runs, and runs it there, so the agent's process stays free for agents.

```python
from flowgentic.langgraph import bind_tools
from flowgentic.tools import ToolRuntime, mcp_tool, task_tool

def simulate(x: float) -> dict:          # a top-level function, importable by workers
    """Run a simulation of x."""
    ...

runtime = ToolRuntime(session, service)  # a RHAPSODY Session; a ToolServiceClient
tools = bind_tools(runtime, [
    task_tool(simulate, suspend=True),   # a RHAPSODY task; the agent suspends meanwhile
    mcp_tool("lookup", description="Look a material up.", args_schema=LookupArgs,
             idempotent=True),           # through the tool service
    take_note,                           # a LangChain @tool: inline
])
```

| Tool | Made with | Runs | For |
| --- | --- | --- | --- |
| Inline | the framework's own, such as LangChain's `@tool` | in the agent's process, on its event loop | short Python |
| Task | `task_tool(fn)` | as a function task: in the process's RHAPSODY session, or on a backend of the application's AsyncFlow engine | compute the agent waits for |
| Service | `mcp_tool(name, ...)` | in the tool service, through its MCP pool | tools on MCP servers |

A task tool's function must be importable by the workers: a top-level
function. It may be `async def`; the worker then runs it on an event loop of
its own, until it ends, as RHAPSODY does with a coroutine function. A service
tool's arguments reach the tool service as JSON values, so the service needs
none of the agent's classes.

`flowgentic.tools` needs no extra to import. Task tools need the `rhapsody`
extra when they run on a session, and the `asyncflow` extra on an engine. MCP
tools need the [tool service](tool-service.md), and `bind_tools` needs the
`langgraph` extra. Flowgentic never creates a RHAPSODY backend: the application
creates the `Session` on whatever fits its machine, such as a thread pool on a
laptop or Dragon on an HPC allocation.

## In an AsyncFlow application

An application whose workflow runs on AsyncFlow can run its agents' task tools
as tasks of its own engine. What an agent decides then runs beside the
workflow's other tasks, on the same backends, and shows in the same telemetry:

```python
from radical.asyncflow import WorkflowEngine

flow = await WorkflowEngine.create(backend=[compute, ai])  # the application's engine
runtime = ToolRuntime(engine=flow, backend="compute")
tools = bind_tools(runtime, [
    task_tool(run_kernel, suspend=True),                  # on "compute"
    task_tool(score, backend="ai"),                       # a tool may name its own backend
])
```

Each call of a task tool submits one AsyncFlow function task, on the backend
the tool names, else on the runtime's `backend`, else on the engine's default.
Suspension, repeat safety and the host's registrations work as on a session.
The function may be `async def`, and its arguments may have any names, those of
AsyncFlow's own call keywords included. AsyncFlow knows the task by the
function's name, and gives it its uid, such as `task.000007`, when the runtime
submits it.

Agents that use an engine's task tools run on the engine's event loop: in a
coroutine, a block or an agent host the application runs there, not inside an
AsyncFlow task (as `bind_agent` runs an agent), whose function runs on an event
loop of its own in a pool thread. A task tool called off the engine's loop
raises `RuntimeError`, which fails its agent: AsyncFlow would never register
the task.

On an engine, a tool's task fields are `backend` and
`task_backend_specific_kwargs`, the backend's own fields such as resources,
which AsyncFlow passes to the backend as the task's `task_description`. Any
other field is a `ValueError`, and so is a backend the engine lacks: when the
runtime is made, for its own `backend`, or when the tool is bound
(`bind_tools`, through `ToolRuntime.check`), for the tool's. On a session,
`backend` names the RHAPSODY backend, checked in the same way against the
session's backends. A tool called without being bound, on a runtime that
cannot run it, raises `RuntimeError`, which fails its agent. A runtime has a
session or an engine, not both.

AsyncFlow runs only `async def` tasks, so the runtime wraps a sync function
in one; the function itself is called as on a session, with no event loop
running in its thread, so it may run one of its own, as `asyncio.run` does.

The engine is the application's: Flowgentic neither starts it nor shuts it
down. AsyncFlow 0.7.0 keeps every task, with its arguments and value, or a failed
task's exception and its traceback, until the engine shuts down, so a
campaign's memory grows with the tasks its agents ask for. A task still
running when the engine shuts down ends with `TaskCancelled`, and so does one
asked for after.

A task belongs to the workflow scope or block its agent runs in, as if the
agent had called it there: AsyncFlow labels the task with that scope's id,
which its telemetry reports, and makes a task asked for in a block one of the
block's. Cancelling the block cancels the task, which ends with
`TaskCancelled`, whether its agent waits for it in memory or is suspended in a
host the block runs.

## Suspension

A task tool made with `suspend=True` suspends its agent while the task runs:
the agent's state goes to the [checkpointer](checkpointer.md) and leaves memory,
and the [agent host](agent-host.md) resumes the agent when the task ends.
`suspend` may also be a function of the agent's id and the call's arguments,
for example to suspend only for long simulations. Without suspension, the
agent waits in memory, which costs nothing to save and restore.

With LangGraph, the task tool submits its task, then calls `interrupt()`. On
resume, LangGraph runs the tool again; the runtime finds the task already
submitted, and the tool returns its result. The user's function runs once, in
a RHAPSODY worker. With `route_tool_calls`, each tool call is its own step, so
the agent's other tool calls do not run again either.

## Repeat safety

A task or service call is keyed by its agent, the LangGraph task that runs
the tool, and the tool call's id: `agent/task/call`. A call made again with the
same key, as a framework does when it resumes an agent, gets the first call's
result and runs nothing: LangGraph gives a task the same id when it runs again.
A later turn that reuses a call id, as a model server that numbers its tool
calls per message does, runs in a new task, so it makes a new call. Outside a
LangGraph run the key is `agent/call`. The runtime keeps the last `retained`
ended tasks (100,000 by default), and an agent in a host finds the tasks it
asked for until it ends, however many have ended since. The tool service keeps
its own answers, by the same key.

The task tells turns apart only when each turn's tools run in a LangGraph task
of their own, as they do in a `ToolNode` node, with `route_tool_calls`, or in a
subgraph built that way. A node that loops between the model and its tools
itself runs every turn in one task: there, a model server that numbers its tool
calls per message gets an earlier turn's result for a later call with the same
id. Give the tools a node of their own, or use a model server that makes unique
call ids, as vLLM and OpenAI do.

Cancelling an agent, or a task tool's wait, leaves a task that has started
running: the task belongs to every call with its key, and RHAPSODY does not
stop a task that has started. On an engine, cancelling the AsyncFlow block the
agent runs in cancels its tasks too (see
[In an AsyncFlow application](#in-an-asyncflow-application)). The task keeps its place in its `TaskOrder` until
it ends, and the call made again gets its result. A task its order has not
admitted yet is withdrawn once no call waits for it, such as when every agent
that asked for it is cancelled or its host stops: it never reaches RHAPSODY,
and the call made again submits it afresh.

A task that RHAPSODY or AsyncFlow cancels, or whose session closes or engine
shuts down before it ends, ends with `TaskCancelled`: its place in its order is
given back, and the model reads that the call failed. A task that its order
admits after the session has closed, or the engine has shut down, fails at
once, in the same way. On an engine, cancelling the future that
`runtime.submit` returned also asks the task's backend to cancel it. A thread
or process pool, on an engine or a session, cannot stop a function that has
started: it runs to its end, while the task has already ended `cancelled` and
given back its place in its order.

A task's RHAPSODY uid is `flowgentic.` and its key, with `/` written `.`, `_`
written `__`, and any character other than a letter, a digit or `-` written
`_x<hex>_`, so different calls never share a uid. AsyncFlow names its tasks
itself; the [decision record](#the-decision-record) holds each one's uid.

An MCP call that lost its connection may or may not have run on the server. It
is sent again only for a tool declared `idempotent=True`, such as a lookup.

## What the model sees, and what the worker runs

A task tool's arguments for the model can differ from its function's:

- `args_schema`, a pydantic model, gives the arguments the model sees;
- `prepare(agent_id, call_id, args)` returns the keyword arguments the
  function is called with, adding what the model must not see, such as where
  to write a result;
- `output(value)` turns the function's result into what the model reads.

`prepare` runs once per task, when the task is submitted: an agent that
resumes finds its task and does not call it again. A model server that numbers
its tool calls per message reuses call ids on later turns, so a `prepare` that
names a file after the call should not use `call_id` alone; a random part,
such as a `uuid4()`, is safe, since `prepare` runs once. A failed, refused or lost
call reaches the model as the tool's error, so the agent can read it and
decide what to do. A runtime set up wrong, without the session or engine, or the
started service client, that a tool needs, raises `RuntimeError` instead: with
`ToolNode`'s default error handling, the agent fails.

## Order of tasks

When tasks reach RHAPSODY or AsyncFlow is a policy, a `TaskOrder`: `admit(info)` returns
when a task may be submitted, or raises to refuse it, and `done(info)` is
called when an admitted task ends. `Immediate()`, the default, submits every
task at once. A policy can hold tasks and release them in an order of its
choosing, such as a campaign's priorities, or refuse them, as a budget does.

## The decision record

Each task ends in a `TaskRecord`, which `runtime.add_listener(listener)` hands
to each listener before the task's agent is told. It ties the task to the
decision that asked for it, the agent (`record.info.agent_id`) and its tool
call (`record.info.call_id`), and says where the task ran and when:

| Field | What |
| --- | --- |
| `uid` | The task's uid in AsyncFlow or RHAPSODY; `None` if it was never submitted |
| `backend` | The backend it was for: the tool's, else the runtime's, else, on an engine, the engine's default, and on a session the one the session sent it to |
| `t_commit` | When the agent committed to the call: when the model reply that made the call reached the agent, for a model run through a [fleet](fleet.md), or through the [agent loop](agent-loop.md)'s transport, in an agent the [host](agent-host.md) runs; otherwise `t_submit` |
| `t_submit` | When the runtime got the call |
| `t_admit` | When its order admitted it and the runtime submitted it; `None` if it never was |
| `t_start` | When the backend reported it running: AsyncFlow's `TaskStarted` event, from the telemetry the runtime was given |
| `t_finish` | When it ended: when the agent's process saw AsyncFlow resolve the task |
| `t_end` | When the runtime saw it end, just before its agent is told |
| `outcome` | `ok`, `error`, `refused` or `cancelled`, with its `value` or `error` |

Times are `time.time()` in the agent's process, which on an engine is the
engine's too. A withdrawn task is `cancelled` with no admission time.

The fleet notes each reply with the ids of its tool calls, so a call that no
reply made, such as one a graph node makes itself, commits when the runtime
gets it. Some servers number the calls of each reply (`call_0`, `call_1`), so
if one agent's parallel branches get replies that reuse an id, each such call
is dated by the latest of those replies.

`t_finish` is stamped on the event loop's first pass after AsyncFlow resolves
the task's future, which it does as the backend reports the task's end, just
before it emits its own end event. It needs no telemetry. On a session it is
`None`.

`t_start` comes from AsyncFlow's telemetry, which the application starts and
hands to the runtime:

```python
telemetry = await flow.start_telemetry()  # needs rhapsody-py[telemetry]
runtime = ToolRuntime(engine=flow, backend="compute", telemetry=telemetry)
```

The telemetry must be that engine's own: the engine emits to no other, so
another engine's is a `ValueError`.

The telemetry delivers its events in batches, after the fact. A task whose
`TaskStarted` event has not arrived when the task ends, as can happen in a
burst of thousands of tasks, has no `t_start` in its record; the event is still
in the telemetry, under the record's `uid`, for a join after the run. A backend
that never reports a task running, such as AsyncFlow's own
`LocalExecutionBackend`, gives no `t_start`. Without telemetry, and on a
session, `t_start` is `None`. `t_start` is when the backend reports the task
running, which is not always when its function starts: RHAPSODY's thread and
process pools report it when they hand the task to their pool, which runs it
once a worker is free.

From one agent's records: the time from a decision to its task's
submission is `t_admit - t_commit`, the framework's way from the model's reply
to the tool call and then the order's wait; the backend holds the task for
`t_finish - t_start`, which on RHAPSODY's pools includes its wait for a free
worker; and the runtime hands its result over `t_end - t_finish` after it
ends. The time an agent takes to
decide again, once a task's result has reached it, is the next record's
`t_commit` minus this one's `t_end`, less the host's resume when the agent
suspended.

## What it reports

- `runtime.add_listener(listener)` receives each task's
  [decision record](#the-decision-record).
- `runtime.stats()` counts the tasks submitted, asked for again, failed and
  refused.
- Each task or service call is an `execute_tool` span, inside its agent's
  trace, with `gen_ai.tool.name`, `gen_ai.tool.call.id`,
  `flowgentic.tool.kind` (`task` or `service`) and `flowgentic.tool.outcome`.
- `flowgentic.tool.duration` is a histogram of the time from a call's
  submission to its result, by tool, kind and outcome.
