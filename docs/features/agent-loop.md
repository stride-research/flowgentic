# Agent loop

`flowgentic.loop.LoopRunner` is Flowgentic's own agent loop: a
[runner](agent-host.md#runners) for the agent host, on OpenAI-format messages
and the fleet's transports, without LangChain or LangGraph. It runs a
tool-calling agent: the model replies, the tool calls of its reply run at
once, their results go back to the model, and so on until the model answers.

It runs the agent that `GraphRunner` runs with a ReAct graph, written as a
`while` loop, on the same parts: the [fleet](fleet.md), [task and MCP
tools](tools.md), the [tool service](tool-service.md) and a
[key-value store](checkpointer.md). `GraphRunner` stays, for graphs and for
what LangGraph adds.

```python
from flowgentic.fleet import Fleet, OpenAITransport, TokenAdmission
from flowgentic.host import Agent, AgentHost
from flowgentic.loop import LoopRunner
from flowgentic.store import MemoryStore
from flowgentic.tools import ToolRuntime, task_tool


async def note(text: str) -> str:
    """Write a note in the agent's log."""
    return f"noted: {text}"


replicas = [OpenAITransport("Qwen3-8B", base_url=url) for url in urls]
fleet = Fleet(replicas, admission=TokenAdmission(tokens=40_000, requests=64))
runner = LoopRunner(
    fleet,
    [task_tool(simulate, suspend=True), note],  # a RHAPSODY task, an inline tool
    runtime=ToolRuntime(session),
    store=MemoryStore(),  # RadexStore.attach(...) on an allocation
)
report = await AgentHost(runner).run(
    Agent(f"agent-{i}", task) for i, task in enumerate(tasks)
)
```

`flowgentic.loop` needs no extra; `OpenAITransport` needs the `openai` extra,
which the `agents` extra, the agents-at-scale path's, includes.
[Agents at scale](../guides/agents-at-scale.md) runs a campaign of such agents
on a laptop.

## What it does

- **Its messages are OpenAI Chat Completions dicts**: `role`, `content`,
  `tool_calls` with JSON-string arguments, `tool_call_id`. The output's
  messages, the state a suspension saves and the trim hook all use them.
- **It calls the model through the fleet**, which routes and admits each call
  by agent, or through a transport, such as `OpenAITransport`; see
  [Fleet](fleet.md#replicas-are-transports). Each call sends the tools' JSON
  schemas, built once, and `model_kwargs`, such as `max_tokens`.
  `system_prompt` goes first in every call; it is not part of the agent's
  history. Each model call is a GenAI `chat` span with its usage.
- **Its tools' schemas come from pydantic**: a task or MCP tool's
  `args_schema`, or the function's signature and type hints. An inline tool is
  a plain function, `async` or not; one with `name`, `description` and
  `args_schema` attributes uses them.
- **It runs the calls of one reply concurrently.** Task tools run as RHAPSODY
  tasks and MCP tools through the tool service, on the `ToolRuntime`; inline
  tools run in the agent's process. The results go back to the model in the
  order of the calls.
- **It suspends an agent on a task tool that suspends.** The loop saves the
  agent's state, its messages and its count of model calls, to the store with
  one put, and returns `Suspended`. The host awaits the task; the loop reads
  the state back with one get, and goes on. While suspended, the agent holds
  no memory in the process.
- **It deletes an agent's state when the agent ends,** however it ends, the
  host's cancelling it while it waits included. The store holds the agents
  that are suspended, or running since a suspension: what a Dragon dictionary,
  whose size is fixed, needs. An agent that never suspends never touches the
  store.
- **It never runs a call twice.** A task or MCP call is keyed by the agent, the
  turn (the number of the model call that made it) and the call's id. When the
  agent resumes, its task is found, not run again. A later turn that reuses a
  call id, as a model server that numbers its calls per reply does, makes a
  new call.
- **It returns tool errors to the model.** A tool that raises, arguments its
  schema refuses, a tool that does not exist, arguments that are not valid
  JSON, a task tool's `prepare` or `suspend` that raises, a failed task or MCP
  call: each becomes a tool result that says so, and the agent goes on. A runtime set up wrong, without the
  RHAPSODY session or the service client a tool needs, fails the agent
  instead, as with LangGraph's tools.
- **It limits model calls.** `max_model_calls` (25 by default) counts the
  agent's calls over its whole run, suspensions included. The call past it
  fails the agent with `ModelCallLimitExceeded`, which the host counts in
  `report.errors`.
- **It returns structured answers.** With `response_format=Answer`, a
  pydantic model, the model answers by calling a tool named `Answer`, on its
  own in its reply. Its arguments are validated; a refusal goes back to the
  model, which tries again. A plain reply that parses as the model's JSON is
  accepted too; any other plain reply gets a request to call the tool.
- **It trims the history with a hook.** `trim(messages)`, a function or a
  coroutine function, is called before each model call, and what it returns
  is the agent's history from then on: what the model sees, what a suspension
  saves, and what the output holds. The count of model calls is kept apart, so
  trimming never resets the limit. Keep each tool call with its result.
- **It stops an agent on an interrupt.** An inline tool that raises
  `Interrupt(value)` ends its agent as `interrupted`. The agent's result holds
  an `Interruption` with the value, the tool call and the history, in which
  that call has no result. Nothing stays in the store.

A finished agent's output, passed to the host's `on_done`, is a `LoopOutput`:
`messages`, `structured_response` (or `None`) and `model_calls`. An agent's
input is a string, a message (a dict with a `role`), a list of messages, or a
dict with `messages`, as a LangGraph graph takes it; anything else fails the
agent with a `TypeError` before its first model call. The loop copies the
input and never changes the caller's list. `Agent.config` is not read.

## LangChain, through its adapter

The loop runs no LangChain code of its own. With the `langgraph` extra,
`flowgentic.langchain` adapts it:

- a LangChain chat model given as the model, or as the fleet's replicas, runs
  as a transport (`ChatModelTransport`), and the fleet's `FleetChatModel` runs
  as its fleet;
- a LangChain tool given as a tool runs inline (`inline_tool`), with its own
  name, description and schema, called with the call's id as LangGraph's
  `ToolNode` calls it, so an `InjectedToolCallId` argument gets the id; an
  `Interrupt` it raises stops its agent. A tool that needs what a graph
  injects (`InjectedState`, `InjectedStore`) is refused, and so is a task or
  MCP tool made by `flowgentic.langgraph.bind_tools`: give the loop the
  `task_tool` or `mcp_tool` declaration itself;
- LangChain messages given as an agent's input are converted.

The loop's messages stay OpenAI-format dicts throughout. The adapter converts
them to LangChain's for each call, which costs CPU: see the measurements
below.

## What it reports

`runner.stats()` counts model calls, tool calls and their errors,
suspensions, interrupts, and the store's puts, gets and deletes. A deletion
that fails is logged and counted in `delete_failures`; the agent's outcome
stands.

The agent's states are counted as the host's other parts count them: model
calls in `MODEL_QUEUED` and `MODEL_REQUEST` (through the fleet, or around a
plain chat model's call), waits on tasks in `TOOL_WAIT`, tool service calls in
`TOOL_CALL`, the loop and its inline tools in `WORKING`. Task and MCP calls are
`execute_tool` spans under the agent's `invoke_agent` span, and each
suspension is the host's `flowgentic.agent.suspend` event; see
[Telemetry](telemetry.md).

## Compared with `GraphRunner`

| | `LoopRunner` | `GraphRunner` |
| --- | --- | --- |
| The agent | a `while` loop over a model and tools | any compiled LangGraph graph |
| Messages | OpenAI-format dicts | LangChain messages |
| Needs | no extra (`openai` for `OpenAITransport`) | LangGraph |
| A suspension writes | one put of the messages | a checkpoint and its pending writes |
| A resume reads | one get | the checkpoint and its writes |
| A finished agent's state | deleted | kept, or deleted with `delete_finished=True` |
| A limit | `max_model_calls`, over the whole run | `recursion_limit` per run, and as many resumes |
| Tool errors | always returned to the model | as the graph's `ToolNode` handles them |
| Structured answers | `response_format` | the graph's own |
| Human in the loop | an interrupt ends the agent | an interrupted agent can resume |
| Streaming, middleware, subgraphs | no | LangGraph's |

To measure both on the [agents-at-scale](../guides/agents-at-scale.md)
example's workload, in one agent process:

```bash
uv run --extra dev python benchmarks/agent_loop.py --runner loop --agents 5000
uv run --extra dev python benchmarks/agent_loop.py --repeat 3 --sizes 1000,5000
```

`--runner` is `loop` (no LangChain on the loop's path), `loop-langchain` (the
same agents through LangChain's adapter) or `graph`. Each run prints one JSON
line: wall and CPU time, the event loop's lag, the collector's pauses, peak
RSS, and the store's calls and bytes. `benchmarks/client_cost.py` measures a
model client's CPU per call against a local OpenAI-compatible stub, for
`OpenAITransport`, the `openai` SDK alone, LangChain's `ChatOpenAI` and the
adapter.
