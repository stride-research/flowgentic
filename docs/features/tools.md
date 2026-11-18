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
| Task | `task_tool(fn)` | as a RHAPSODY function task in the process's session | compute the agent waits for |
| Service | `mcp_tool(name, ...)` | in the tool service, through its MCP pool | tools on MCP servers |

A task tool's function must be importable by the workers: a top-level
function. It may be `async def`; the worker then runs it on an event loop of
its own, until it ends, as RHAPSODY does with a coroutine function. A service
tool's arguments reach the tool service as JSON values, so the service needs
none of the agent's classes.

`flowgentic.tools` needs no extra to import. Task tools need the `rhapsody`
extra when they run, MCP tools need the [tool service](tool-service.md), and
`bind_tools` needs the `langgraph` extra. Flowgentic never creates a RHAPSODY
backend: the application creates the `Session` on whatever fits its machine,
such as a thread pool on a laptop or Dragon on an HPC allocation.

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

A task or service call is keyed by its agent and the tool call's id. A call
made again with the same key, as a framework does when it resumes an agent,
gets the first call's result and runs nothing. The runtime keeps the last
`retained` ended tasks (100,000 by default), and an agent in a host finds the
tasks it asked for until it ends, however many have ended since. The tool
service keeps its own answers.

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
resumes finds its task and does not call it again. A failed, refused or lost
call reaches the model as the tool's error, so the agent can read it and
decide what to do. A runtime set up wrong, without the RHAPSODY session or the
started service client a tool needs, raises `RuntimeError` instead: with
`ToolNode`'s default error handling, the agent fails.

## Order of tasks

When tasks reach RHAPSODY is a policy, a `TaskOrder`: `admit(info)` returns
when a task may be submitted, or raises to refuse it, and `done(info)` is
called when an admitted task ends. `Immediate()`, the default, submits every
task at once. A policy can hold tasks and release them in an order of its
choosing, such as a campaign's priorities, or refuse them, as a budget does.

## What it reports

- `runtime.add_listener(listener)` receives a `TaskRecord` as each task ends:
  its info, when it was asked for, admitted and ended, its outcome (`ok`,
  `error`, `refused` or `cancelled`), and its value or error.
- `runtime.stats()` counts the tasks submitted, asked for again, failed and
  refused.
- Each task or service call is an `execute_tool` span, inside its agent's
  trace, with `gen_ai.tool.name`, `gen_ai.tool.call.id`,
  `flowgentic.tool.kind` (`task` or `service`) and `flowgentic.tool.outcome`.
- `flowgentic.tool.duration` is a histogram of the time from a call's
  submission to its result, by tool, kind and outcome.
