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
from flowgentic.fleet import Fleet, HTTPTransport, TokenAdmission
from flowgentic.host import Agent, AgentHost
from flowgentic.loop import LoopRunner
from flowgentic.store import MemoryStore
from flowgentic.tools import ToolRuntime, task_tool


async def note(text: str) -> str:
    """Write a note in the agent's log."""
    return f"noted: {text}"


replicas = [HTTPTransport("Qwen3-8B", base_url=url) for url in urls]
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

`flowgentic.loop` needs no extra, and neither does `HTTPTransport`, the
fleet's default transport; the `agents` extra, the agents-at-scale path's,
adds RHAPSODY and MCP for the tools.
[Agents at scale](../guides/agents-at-scale.md) runs a campaign of such agents
on a laptop.

## What it does

- **Its messages are OpenAI Chat Completions dicts**: `role`, `content`,
  `tool_calls` with JSON-string arguments, `tool_call_id`. The output's
  messages, the state a suspension saves and the trim hook all use them.
- **It calls the model through the fleet**, which routes and admits each call
  by agent, or through a transport, such as `HTTPTransport`; see
  [Fleet](fleet.md#replicas-are-transports). Each call sends the tools' JSON
  schemas, built once, and `model_kwargs`, such as `max_tokens`, or the
  call's own, from a function: see [each call's
  arguments](#each-calls-arguments). `system_prompt` goes first in every
  call; it is not part of the agent's history. Each model call is a GenAI
  `chat` span with its usage.
- **Its tools' schemas come from pydantic**: a task or MCP tool's
  `args_schema`, or the function's signature and type hints. An inline tool is
  a plain function, `async` or not; one with `name`, `description` and
  `args_schema` attributes uses them. A sync one runs in a worker thread of
  the runner's own (up to `min(32, CPUs + 4)`), with the agent's context, as
  LangChain's `ToolNode` runs one, so that a slow tool does not hold the
  event loop, and every agent of the process with it; not on the loop's
  default executor, which also resolves host names for the model's
  connections. So a sync inline tool must be thread-safe: a `sqlite3`
  connection made on another thread, for one, refuses to work there; make it
  in the tool, or with `check_same_thread=False` and a lock, or make the tool
  `async def`, which runs on the loop. `runner.close()` ends the threads,
  once their calls end, as collecting the runner does.
- **It runs the calls of one reply concurrently.** Task tools run as RHAPSODY
  tasks and MCP tools through the tool service, on the `ToolRuntime`; inline
  tools run in the agent's process. The results go back to the model in the
  order of the calls, their text as written, not escaped to ASCII. Each call
  is an `execute_tool` span, an inline tool's too (`flowgentic.tool.kind`
  `inline`), with status ERROR and its exception when the tool raised. An
  inline tool reads the agent it runs for with `flowgentic.host.current_agent()`,
  sync or async. A task's
  [decision record](tools.md#the-decision-record) commits at the reply that
  asked for it, through a fleet or a transport alone. A task tool the runtime
  cannot run, such as one naming a backend the session lacks, is refused when
  the runner is made (`ToolRuntime.check`).
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
  turn (a token of the runner's own and the number of the model call that
  made it) and the call's id. When the agent resumes, its task is found, not
  run again. A later turn that reuses a call id, as a model server that
  numbers its calls per reply does, makes a new call, and so does another
  runner in the process whose agents have the same ids, through its own
  runtime or a shared one. The token is saved with a suspended agent's state,
  so a runner that resumes an agent another runner suspended, through a
  shared runtime and store, finds the agent's task too.
- **Runners that share a store share its keys.** A suspended agent's state is
  under `key_prefix` and its id, so two runners on one store whose agents can
  have the same ids, as two populations numbered from `agent-0`, must each
  have their own `key_prefix`: otherwise one's suspension overwrites the
  other's state. The runner cannot tell this from a hand-over, in which a
  second runner resumes the first's agents on purpose, so it does not check
  it.
- **It returns tool errors to the model.** A tool that raises, arguments its
  schema refuses, a tool that does not exist, arguments that are not valid
  JSON, a task tool's `prepare` or `suspend` that raises, a failed task or MCP
  call: each becomes a tool result that says so, and the agent goes on, a
  `RuntimeError` of the tool's own included, such as `NotImplementedError`.
  A runtime set up wrong, without the RHAPSODY session or the service client
  a tool needs, fails the agent instead with `ToolSetupError`, a
  `RuntimeError`, as with LangGraph's tools.
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
input is a string, a message (a dict with a `role`, or a `(role, content)`
tuple), a list of messages, or a dict with `messages`, as a LangGraph graph
takes it; anything else fails the agent with a `TypeError` before its first
model call. The loop copies the input and never changes the caller's list.
`Agent.config` is not read. Tool calls in an input history run first only if
nothing but tool results follows them, as in a conversation cut at a tool
call. A call that a later message moved past without a result gets one that
says it was not run, before that message: a server requires a result for
every call, and such a history, as when a user asked something else instead,
is still a conversation the agent can go on with, which failing the agent
would refuse. Text that UTF-8 cannot carry, such as a lone surrogate in a
file name Linux could not decode, reaches the model as its escape.

## Each call's arguments

`model_kwargs` is a dict that goes with every model call, or a function of the
agent's id and its messages that returns one call's. A coroutine function
works too. The function is called once per model call, after `trim`, with the
history the call sends, without the system prompt; it must not change it.
What it returns is all the call's keywords: nothing of a dict is merged in.
With a fleet, `reserve` and `meta` among them are the fleet's: tokens to
admit beyond the usual reply, and what the call's `CallRecord` carries. The
other keywords go to the replica, as `max_tokens` and `extra_body` do. Each
try of a call, when the fleet's `Recovery` tries it again, sends the same
keywords.

A reasoning knob, for one: an agent's decision calls, the calls after an
analysis, think within the agent's own budget, and its other calls do not
think.

```python
THINK_OFF = {"chat_template_kwargs": {"enable_thinking": False}}


def thinking(agent_id: str, messages: list[dict]) -> dict:
    last = messages[-1]
    if last["role"] != "tool" or not is_analysis(last):
        return {"max_tokens": 512, "extra_body": THINK_OFF}
    budget = budgets[agent_id]
    return {
        "max_tokens": 512 + budget,
        "extra_body": {
            "chat_template_kwargs": {"enable_thinking": True},
            "thinking_token_budget": budget,
        },
        "reserve": budget,  # the fleet admits the call with its thinking
        "meta": {"budget": budget},
    }


runner = LoopRunner(fleet, tools, model_kwargs=thinking)
```

With `GraphRunner`, the graph's model node is the application's own, so it
does the same itself: it computes the arguments from the state's messages and
the agent (`current_agent()`, or the run's `thread_id`), and passes them with
the call, as `FleetChatModel`'s `ainvoke(messages, reserve=budget, **kwargs)`
or the fleet's `ainvoke(agent, messages, **kwargs)`.

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
`TOOL_CALL`, the loop and its inline tools in `WORKING`. Task, MCP and inline
tool calls are `execute_tool` spans under the agent's `invoke_agent` span, and each
suspension is the host's `flowgentic.agent.suspend` event; see
[Telemetry](telemetry.md).

## Compared with `GraphRunner`

| | `LoopRunner` | `GraphRunner` |
| --- | --- | --- |
| The agent | a `while` loop over a model and tools | any compiled LangGraph graph |
| Messages | OpenAI-format dicts | LangChain messages |
| Needs | no extra | LangGraph |
| A suspension writes | one put of the messages | a checkpoint and its pending writes |
| A resume reads | one get | the checkpoint and its writes |
| A finished agent's state | deleted | kept, or deleted with `delete_finished=True` |
| A limit | `max_model_calls`, over the whole run | `recursion_limit` per run, and as many resumes |
| Tool errors | always returned to the model | as the graph's `ToolNode` handles them |
| Structured answers | `response_format` | the graph's own |
| Each model call's arguments | `model_kwargs`, a dict or a function | the graph's model node |
| Human in the loop | an interrupt ends the agent | an interrupted agent can resume |
| Streaming, middleware, subgraphs | no | LangGraph's |

Two differences an application that moves from `GraphRunner` to the loop may
see:

- **When a task tool's `suspend` is asked.** The loop asks it before it
  submits the task, so a `suspend` that raises submits nothing.
  `GraphRunner`'s task tools ask it after `prepare`, once the task is
  submitted. An application that counts calls in `prepare` counts
  differently.
- **The prompt's length.** The loop renders the tools' schemas through
  pydantic, as above; LangChain renders them with `convert_to_openai_tool`.
  The model gets the same tools, but a call's prompt can differ by a few
  tokens, the same for every call with the same tools: one application
  measured 27 more prompt tokens per call on the loop than through
  LangChain, and its model made the same choices.

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
`HTTPTransport`, `OpenAITransport`, the `openai` SDK alone, LangChain's
`ChatOpenAI` and the adapter.
