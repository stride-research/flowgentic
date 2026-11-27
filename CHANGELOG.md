# Changelog

All notable changes to Flowgentic are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Tags `v0.1.0` (`3e43c13c`, 23 Sep 2025) and `0.2.0` (`72e972d4`, 2 Oct 2025)
predate this changelog, so their changes are not listed here.

## [0.7.0]

Agents at scale can serve: a host takes agents one at a time, as a server's
messages arrive, and answers each with a future of how its agent ended. The
processes of a campaign split each model server's caps exactly, and each can
send to a few servers only. Task tools are placed call by call, and run again
when their worker is lost, and the tool runtime keeps its ended tasks'
outcomes, not the tasks. Everything is added: nothing of 0.6.0's API is
removed or renamed, and the dependency ranges are 0.6.0's. Changed lists what
behaves differently.

The new public API, detailed under Added:

- **Agents on demand.** `AgentHost.start()`, `submit(agent)`, `stop(drain=)`
  and `async with host:`; `AgentHost(max_queued=...)` and
  `flowgentic.host.QueueFull`.
- **Exact caps across agent processes.** `Fleet(process=(index, processes))`,
  `Fleet(layout="spread" | "pack")` and `flowgentic.fleet.Share`.
- **Placement call by call.** `ToolRuntime(place=...)`.
- **Lost workers.** `task_tool(fn, retries=..., retry_on=...)`, an optional
  `TaskOrder.again(info)`, the outcome `lost`, `TaskRecord.attempt`,
  `TaskInfo.attempt`, `lost_on` and `lost_error`, `ToolRuntime.uid(key,
  attempt)`, and the metric `flowgentic.tool.retries`.

### Added

- **Agents on demand.** A host takes agents one at a time, as messages reach
  a server, between `AgentHost.start()` and `AgentHost.stop()`, with the cap,
  staggered starts, the collector's settings, suspension, `AgentStates` and
  `on_done` that `run` gives. `submit(agent)` never blocks: it returns a
  future of `(result, output)`, what `on_done` receives, and the agent starts
  as soon as a slot is free, after the agents submitted before it. Cancelling
  the future cancels the agent. An agent's id must be unique among the live
  and waiting agents, and is free again once the agent has ended.
  `AgentHost(max_queued=...)` bounds the agents waiting for a slot; beyond it,
  `submit` raises the new `flowgentic.host.QueueFull`. `stop()` waits for the
  live and waiting agents, and still takes the agents the host's own agents
  submit meanwhile; `stop(drain=False)` cancels the live ones and withdraws
  the waiting ones. `async with host:` starts the host and stops it at the
  block's end, with `stop(drain=False)` if the block raised. `run` and
  `run_indexed` run on the same parts, and behave as before. A started host
  freezes the collector's objects in `start()`, after the full collection it
  runs anyway, so that no request waits for the freeze; `run` and
  `run_indexed` still freeze once their first wave has started.
- The agents-at-scale example's `--arrivals RATE`: its agents arrive one at a
  time, and each process's host takes them as they come. The host
  benchmark's `--submit` measures the same path.
- **Exact admission caps across agent processes.**
  `Fleet(process=(index, processes))` says which of the processes that use
  the replicas this one is, and the processes split each replica's caps so
  that their parts add up to the cap and none is above it. Each process
  holds an equal part of the fleet's requests, to one request, and its part
  of the token cap follows its requests: the token cap ÷ the request cap for
  each. A process may hold none of a replica, and then sends it nothing: it
  routes each agent, by the agent's hash, to one of the replicas it holds a
  part of, in proportion to its parts. Every process must be given the same
  replicas, in the same order, and the same admission. `Fleet(share=...)`
  works as before, and `Fleet` refuses both together. Over 4 replicas with
  a cap of 256 requests, 100 processes with `share=1 / 100` used 200 of
  each replica's 256, and 300 processes with `share=1 / 300` could send 300;
  with `process=`, they use 256 in both.
- **Each process to a few replicas.** `Fleet(process=..., layout="pack")`
  gives each process its part from as few replicas as hold it, in their
  order, rather than from every replica (`layout="spread"`, the default).
  A process then holds whole caps, except at the one or two replicas where
  its run of them starts and ends, and connects to those servers only.
  When the processes divide the replicas evenly, every part is whole. With
  the replicas and processes listed in the order of their nodes, and as
  many of each on every node, each process's replicas are on its node.
- `flowgentic.fleet.Share`, the share an `Admission`'s `gate(share)` gets
  from such a fleet: a float, the fraction of the caps, as before, whose
  `part(cap)` is the process's exact part of a cap. The fleet makes shares.
  `fleet.stats()` gives `process` and `replicas_used`, and a routing's
  `loads` its `weights`, the process's parts of the replicas where they
  differ.
- The agents-at-scale example's processes split the caps with
  `process=(index, processes)`, and its guide says so. The example refuses
  more than 128 processes, 2 replicas × 64 requests.
- `ToolRuntime(place=...)` chooses where each task runs: `place(info)` gets
  each task's `TaskInfo` once its order admits it, and returns fields to add
  to the tool's, such as a `backend`, or the backend's own fields
  (`task_backend_specific_kwargs`), which go over the tool's key by key. So
  the agents of `LoopRunner`, which share one tool list, can spread their
  calls over a process's GPUs, by load or otherwise. Each run it places ends
  in one record, which a placement by load counts; a cancelled task's arrives
  before its worker stops. The merge is shallow: a placement can drop all the
  tool's backend fields (`None`), but not one of them. A `place` that raises,
  or fields its session or engine cannot take, fail the task with
  `ToolSetupError`, which fails its agent, as a tool set up wrong does: the
  model never reads it.
- A task whose worker was lost can run again: `task_tool(fn, retries=...,
  retry_on=...)`, where `retry_on(error)`, synchronous, is the application's
  test of a run's error for a lost worker; Flowgentic matches no error
  itself, and decides whether a lost run runs again, up to `retries`.
  `fn` must be safe to run again with the same arguments: `prepare` runs once
  per call, and a worker reported lost may still be running. The task runs
  again at once, under its call's same future, so its agent, suspended or
  not, sees only the last run's outcome, and its order admits it once. A task
  whose agent has ended and that no call waits for does not run again. Each
  run has its own uid (`ToolRuntime.uid(key, attempt)` on a session) and its
  own `TaskRecord`, with the new `attempt` field; a run whose worker was lost
  ends with the new outcome `lost`, and on an AsyncFlow engine is also a
  failed task among the engine's. A `TaskOrder` may refuse a run again with
  an optional, synchronous `again(info)`, as a budget shared by many tasks
  does: the refused run stays `lost`, and orders without `again` are
  unchanged. Each run again is placed again, and `TaskInfo` gains `attempt`,
  `lost_on` and `lost_error`, so a placement can avoid where the worker was
  lost. `runtime.stats()` gains the keys `lost` and `retries`, which a
  comparison with the whole dict sees. The metric `flowgentic.tool.retries`
  counts the runs again, and the call's `execute_tool` span gains
  `flowgentic.tool.attempts` and a `flowgentic.tool.retry` event per run
  again.

### Changed

- **The host's probes keep running values,** not every sample, so their
  memory stays the same however long a host runs: about 33 KB, where they
  kept 8 bytes for each 100 ms lag sample and 9 for each collection. Every
  figure of `HostReport.loop_lag` and `HostReport.collector` is still exact,
  but the lag's `time_weighted_p99_ms`, which comes from a histogram of fixed
  size: it is within 1 % of the exact value, or 0.1 ms, whichever is larger,
  for a p99 under a day, before it is rounded to 0.1 ms.
- **Every `HTTPTransport` in a process shares one SSL context,** as its pools
  did, one for each value of `SSL_CERT_FILE` and `SSL_CERT_DIR`. Each built
  its own before, about 3 ms and 0.7 MiB: 400 replicas cost a process 1.5 s
  and about 300 MB to set up, and now 0.1 s and a few MB.
- `ToolRuntime` keeps the outcomes of its last `retained` ended tasks, not
  the asyncio tasks: each one's value, its exception, or that it was
  cancelled. A call made again after its task ended gets a new future,
  already done, with that outcome, where it got the ended task itself; the
  outcome is the same, and nothing runs again. An outcome with a small value
  costs the runtime a few hundred bytes, the value included (about 170 on
  CPython 3.12 and 400 on 3.11), where an ended task cost about 1.4 KB more.
  A failed task's costs about 5 KB with small arguments, where it cost 6 KB,
  but still as much as its arguments with large ones: its exception keeps its
  traceback, whose frames keep their locals. A call made again raises the
  same exception with the traceback the task ended with, not one grown by
  the calls that raised it before. A call made again from code that runs on
  no event loop gets the future on the loop of the runtime's last task. The
  process no longer holds 100,000 ended asyncio tasks, which the collector
  walked and `asyncio.all_tasks()` visited each time `asyncio.run` ended, as
  it does for each `async def` task on a thread pool. The default `retained`
  stays 100,000.

### Fixed

- `LoopRunner` keys an agent's tool calls by the agent's run as well, so the
  same agent id run again on one runner, as a server runs one logical agent
  per message, makes its calls afresh. Before, with a model server that
  numbers its calls per reply, a later run found the earlier run's tasks, and
  the tool service its calls, and got their results. The turn part of a
  call's key, and so of its RHAPSODY task's uid, now carries the run's number.

## [0.6.0] - 2026-11-22

Agents at scale run on Flowgentic's own agent loop, on OpenAI Chat Completions
messages and transports to OpenAI-compatible model servers, and LangGraph
becomes optional: LangChain is an adapter, in the `langgraph` extra, and
LangGraph's graphs run on the same parts. Task tools also run as tasks of an
application's AsyncFlow engine, and each task ends in a decision record.
Runners outside Flowgentic get public helpers, and the fleet records each
model call as a span and metrics, which a reader checks after the run, or
which stream to a live dashboard while it runs. The fleet's API breaks, before 1.0: see [What breaks](#what-breaks) and
[Migrating from 0.5](#migrating-from-05). `GraphRunner` and its graphs work as
before.

The new public API, detailed under Added:

- **The agent loop.** `flowgentic.loop.LoopRunner`, on the fleet's
  `Transport` and its `Reply`, `ToolCall`, `InvalidToolCall` and `Usage`;
  `HTTPTransport`, the default transport, and `OpenAITransport`, in the
  `openai` extra; `ModelServerError`, with `ServerUnavailable`,
  `AuthenticationFailed` and `RequestRejected`; LangChain's adapter,
  `flowgentic.langchain`; and the `agents` extra, the path without LangGraph.
- **Task tools on an AsyncFlow engine.** `ToolRuntime(engine=flow,
  backend=...)`, `task_tool(fn, backend=...)` and `ToolRuntime.check`; the
  decision record's `TaskRecord.uid`, `backend`, `t_commit`, `t_start` and
  `t_finish`.
- **For runners outside Flowgentic.** `flowgentic.host.current_agent()`,
  `activity(state)` and `wait(futures)`, and `LoopRunner(model_kwargs=fn)`,
  which gives each model call its own keywords.
- **Fleet telemetry.** One `chat` span per model call; the metrics
  `flowgentic.fleet.call.duration`, `flowgentic.fleet.retries`,
  `flowgentic.fleet.outage.duration` and `flowgentic.fleet.tokens`; and
  `flowgentic.telemetry`, whose `read_export(path)` reads a run's export back
  and whose `Export.check()` and `Export.check_servers()` check it.
- **A live dashboard.** The `otlp` extra and `flowgentic.otlp.configure()`,
  which streams a process's metrics and spans to an OpenTelemetry collector,
  with `OtlpTelemetry.rhapsody_options()` for RHAPSODY's own; and
  `examples/live-dashboard`, a collector, Prometheus, Jaeger and Grafana with
  a dashboard per component, started per user.

### What breaks

- **`Fleet.ainvoke(agent_id, messages, tools=None, ...)` returns a `Reply`,
  not an `AIMessage`.** Pass the tools' schemas, or LangChain tools to
  LangChain replicas, as `tools=`. For an `AIMessage`, call
  `flowgentic.langchain.to_ai_message(reply)`: with a LangChain replica it is
  the replica's own message.
- **`Fleet.bind_tools` is gone.** Pass `tools=` to `ainvoke`, or use
  `fleet.chat_model().bind_tools(tools)`.
- **A fleet listener gets `(record, reply)` with the `Reply`.** It has
  `content`, `tool_calls` and `invalid_tool_calls`, but its calls are
  objects, not dicts: `ToolCall(id, name, args)`, so `call.id` where it was
  `call["id"]`, and `InvalidToolCall(id, name, arguments, error)`, whose raw
  text is `arguments`, not `args`. A listener that reads only `content`, or
  counts the calls, works as before.
- **`FleetChatModel` lives in `flowgentic.langchain`.**
  `flowgentic.fleet.FleetChatModel` still names it, but it is no longer in
  `flowgentic.fleet.__all__`; without LangChain, `from flowgentic.fleet
  import FleetChatModel` is a plain `ImportError`, so import it from
  `flowgentic.langchain`, which names the extra.
- **Installs.** httpx is a core dependency (`httpx>=0.27,<1`), for the
  default transport. `flowgentic.fleet` imports no LangChain and needs no
  extra. The agents-at-scale path installs as `flowgentic[agents]`, RHAPSODY
  and MCP, with no LangGraph, LangChain or `openai` SDK; `GraphRunner`,
  `KeyValueSaver`, LangChain replicas and `fleet.chat_model()` need the
  `langgraph` extra too, and `OpenAITransport` the `openai` extra. The
  `asyncflow` extra needs AsyncFlow 0.7 (`radical-asyncflow>=0.7,<0.8`, was
  `>=0.6,<0.7`).
- **A runtime set up wrong raises `ToolSetupError`**, a `RuntimeError`, where
  it raised `RuntimeError`: code that catches `RuntimeError` still catches
  it, but an agent's error reads `ToolSetupError: ...`. On a session, a task
  tool naming a backend the session lacks is one: it fails its agent, and
  `bind_tools` refuses it, where the session's error reached the model.

What does not break: a fleet built from LangChain chat models (`ChatOpenAI`,
or a subclass of it) keeps working, through `ChatModelTransport`, which
passes LangChain messages and tools to them unchanged; `Recovery`'s check may
return a chat model or a transport; admission, routing, accounting,
`CallRecord` and `fleet.stats()` are unchanged. `GraphRunner`, `bind_tools`
(the tools adapter), `KeyValueSaver` and their graphs are unchanged.

### Migrating from 0.5

**Installs.** Replace `flowgentic[langgraph,rhapsody,mcp]` with
`flowgentic[agents]` for the agent loop, or `flowgentic[agents,langgraph]` to
keep running LangGraph graphs.

**A fleet wrapper of the application's own.** Say it holds a `Fleet` of
`ChatOpenAI` replicas, has a `bind_tools(tools)` that called
`Fleet.bind_tools`, and an `ainvoke` that returned the bound fleet's
`AIMessage` to a LangGraph graph. Only the wrapper changes; the graph, which
calls the wrapper, does not:

```diff
+from flowgentic.langchain import to_ai_message
 ...
-        self.bound = self.fleet
+        self.tools = None
 ...
     def bind_tools(self, tools: list) -> None:
-        self.bound = self.fleet.bind_tools(tools)
+        self.tools = list(tools)
 ...
-        return await self.bound.ainvoke(thread_id, messages, reserve=..., meta=..., **kwargs)
+        reply = await self.fleet.ainvoke(thread_id, messages, tools=self.tools, reserve=..., meta=..., **kwargs)
+        return to_ai_message(reply)  # the replica's own AIMessage
```

A graph's model node that called the fleet itself does the same: `reply =
await fleet.ainvoke(agent_id, messages, tools=tools)`, then returns
`to_ai_message(reply)`, as the agents-at-scale example's LangGraph version
does.

**A fleet listener** reads `call.id`, `call.name` and `call.args` for each of
`reply.tool_calls`, and `call.arguments` for each of
`reply.invalid_tool_calls`.

**From `GraphRunner` to `LoopRunner`**, which is optional. The loop takes the
same task, MCP and inline tools, the same fleet and the same store. Two
differences an application may see:

- The runners ask a task tool's `suspend` at different times: `LoopRunner`
  before it submits the task, so a `suspend` that raises submits nothing;
  `GraphRunner`'s tools after `prepare`, once the task is submitted. An
  application that counts calls in `prepare` counts differently.
- The loop renders the tools' JSON schemas through pydantic, LangChain
  through `convert_to_openai_tool`. The model gets the same tools, but the
  prompt's length can differ by a few tokens per call; see the agent loop
  page.

### Added

- An `agents` extra, `flowgentic[rhapsody,mcp]`: the agents-at-scale path
  without LangGraph, LangChain or the `openai` SDK. CI installs the wheel with
  it alone and runs the agent loop's tests, the default transport's and the
  agents-at-scale example there.
- `flowgentic.loop.LoopRunner`, Flowgentic's own agent loop: a runner for the
  agent host on OpenAI-format messages and transports, with no LangChain or
  LangGraph and no extra, beside `GraphRunner`, which stays. It runs a
  tool-calling agent as a loop over a fleet or a transport, with task tools,
  MCP tools and inline tools (plain functions, their schemas through
  pydantic); the calls of one reply run concurrently. A task tool that
  suspends saves the agent's messages to a `KeyValueStore` with one put, read
  back with one get on resume; the entry is deleted when the agent ends,
  however it ends. Tool errors go back to the model, `max_model_calls` limits
  the agent's model calls over its whole run (`ModelCallLimitExceeded`),
  `response_format` gives a validated structured answer in
  `LoopOutput.structured_response`, a `trim` hook shapes the history before
  each model call, and an inline tool that raises `Interrupt(value)` ends its
  agent as interrupted, with an `Interruption`. The agent's states and spans
  are counted as the host's other parts count them, and each tool call,
  inline ones included, is an `execute_tool` span. A task's decision record
  commits at the reply that asked for it, and a task tool the runtime cannot
  run is refused when the runner is made. See the agent loop page.
- `LoopRunner(model_kwargs=...)` also takes a function, or a coroutine
  function, of the agent's id and its messages, which returns each model
  call's keywords: a thinking budget for an agent's decision calls, for one.
  It is called once per call, after `trim`; with a fleet, `reserve` and `meta`
  among its keywords are the fleet's. With `GraphRunner`, the graph's model
  node passes each call's arguments itself.
- `flowgentic.host.current_agent()`, `activity(state)` and `wait(futures)`,
  for inline tools and for runners of an application's own, such as one whose
  code, not a model, picks each step. `current_agent()` is the id of the agent
  the current coroutine works for, in an inline tool too, sync or async, under
  `LoopRunner` and `GraphRunner`. `activity(state)` counts that agent in
  `TOOL_CALL`, `MODEL_REQUEST`, `MODEL_QUEUED` or `TOOL_WAIT` for a block, and
  refuses any other state with `ValueError`. `wait(futures)` waits for tasks
  without raising their errors, and withdraws a `ToolRuntime` task that its
  order has not admitted yet once every wait on it is cancelled. See the
  agent host page.
- `flowgentic.fleet.Transport`: what a fleet's replica, or the loop's model,
  is. Its `async complete(messages, tools=None, **kwargs)` takes OpenAI-format
  messages, the tools' JSON schemas and the call's keywords, and returns a
  `Reply`: its `content`, `tool_calls` (`ToolCall`, arguments parsed),
  `invalid_tool_calls` (`InvalidToolCall`, for the model to read why), `usage`
  (`Usage`: prompt, completion, cached prompt and reasoning tokens) and the
  `reasoning` text, kept out of the conversation; `Reply.message()` is what
  the conversation keeps.
- `flowgentic.fleet.HTTPTransport`, the default transport, for vLLM, SGLang,
  OpenRouter and OpenAI: one httpx POST to `{base_url}/chat/completions` per
  call, at about a quarter of the CPU per call of `OpenAITransport` (0.8
  against 3.1 to 3.3 ms, against a local stub). It takes the SDK's keywords
  (`extra_body` and `extra_headers` merged as the SDK merges them, the key as
  a bearer token, a 600 s timeout) and retries nothing. A failed call raises
  a `ModelServerError` with the server's message: `ServerUnavailable` (no
  answer, a timeout, a 5xx, or a busy server's 408, 409 or 429, which the
  SDK retries too) and `AuthenticationFailed` (401) are transient, and the
  fleet's `Recovery` waits them out; `RequestRejected` (any other 4xx) is
  not, nor is a completion with no choices or a reply httpx cannot read. A
  connection takes at most 5 s; all of a transport's pools share one SSL
  context, and `aclose()` closes them, as the fleet does for a transport
  that `Recovery` replaces. A given `httpx.AsyncClient` keeps its own timeout
  and headers unless the transport is given its own. httpx becomes a core
  dependency.
- Neither transport sends the environment's `OPENAI_API_KEY` to any server but
  OpenAI's own API (`api.openai.com`): a server on an allocation gets `EMPTY`
  unless given a key, where the `openai` SDK alone sends that key to any
  server.
- `flowgentic.fleet.OpenAITransport`, over the `openai` SDK, for the same
  servers, in a new `openai` extra; and `parse_completion` for such a server's
  reply, which both transports read with. It reads the reasoning from
  `reasoning`, and from `reasoning_content` for older vLLM and SGLang; the
  cached tokens from `usage.prompt_tokens_details`, the reasoning tokens from
  `usage.completion_tokens_details`.
- Both transports open another client, with its own connection pool, as the
  calls they hold in flight grow, since one pool's CPU cost per call grows
  with its calls: `HTTPTransport` for every 8 calls, the cheapest per call
  of 4 to 64 against a local stub, `OpenAITransport` for every 64
  (`calls_per_pool=`, `pools=` to fix their number).
- `flowgentic.langchain`, LangChain as an adapter, in the `langgraph` extra:
  `ChatModelTransport` (any LangChain chat model as a transport; it takes
  LangChain messages and tools as they are, too), `FleetChatModel` (moved
  here), `to_ai_message` and `reply_from_message`, `from_openai_messages` and
  `to_openai_messages`, and `inline_tool` (a LangChain tool as an inline tool
  of the loop, which the loop applies itself).
- Each model call through the fleet, or the loop's transport, is one GenAI
  `chat` span with the usage the server reported, recorded by Flowgentic.
  Through the fleet, the span runs from routing to the reply: its wait for
  admission, every try and any outage waited out, so a call sent again after
  an outage is still one span. It names the agent as
  `gen_ai.conversation.id`, and adds the call's wait for admission
  (`flowgentic.fleet.admission_wait`), the requests sent again
  (`flowgentic.fleet.resend_count`) and the time they took
  (`flowgentic.fleet.recovery_wait`), the gap since the agent's previous call
  (`flowgentic.fleet.gap`) and the tool whose result came just before
  (`flowgentic.fleet.after_tool`). A call that failed or was cancelled has
  status ERROR and its `error.type`. With LangChain replicas and
  OpenTelemetry's LangChain instrumentation on, the instrumentation's span is
  the call's, and Flowgentic records none.
- Four fleet metrics, beside 0.5.0's: `flowgentic.fleet.call.duration`, each
  call's time, whose count is the calls, with `error.type` for those that
  failed or were cancelled; `flowgentic.fleet.retries`, the requests sent
  again once their replica came back, counted as each is admitted;
  `flowgentic.fleet.outage.duration`, each outage `Recovery` watched, with
  `flowgentic.fleet.outage.outcome`, `recovered` or `gave_up`; and
  `flowgentic.fleet.tokens`, the prompt (`input`) and completion (`output`)
  tokens the servers counted at each replica, read when the SDK collects, so
  it costs nothing per call. Counts stay exact through outages and
  cancellations: a retried call is one call and several requests,
  `flowgentic.fleet.request.duration` counts a try cancelled at the replica,
  with `error.type` `CancelledError`, and a call cancelled while it waited
  for admission sent no request.
- `flowgentic.telemetry.read_export(path)` reads back the OpenTelemetry export
  each agent process wrote into a directory of its own (`metrics.jsonl` and
  `spans.jsonl`, the SDK's console exporters' JSON) and merges them into an
  `Export`: the fleets' calls, requests, requests sent again and outages, the
  agent-seconds by state, the agents in each state over time, and the `chat`
  spans with their usage. Processes that write into one file are told apart
  by their resource and their series' start times, and a last line cut short,
  as a process killed at its walltime leaves it, is read around.
  `Export.check()` lists what is missing, such as a process with no
  Flowgentic metrics or a line cut short, or what does not add up, such as
  fewer of a fleet's `chat` spans than its calls, which a full span queue
  causes; with `sampled=True`, for spans sampled per agent, only more spans
  than calls. `Export.check_servers()` compares the fleets' tokens and
  successful requests, from the metrics, with the model servers' own
  counters, and all the `chat` spans' where the fleets made no calls. The
  telemetry page shows how each process writes its export, and what the SDK
  costs and how to cut it: spans sampled per agent, no exemplars, metrics
  every 10 s or less often.
- `benchmarks/agent_loop.py --otel DIR` and `benchmarks/agent_host.py --otel
  DIR` run with the OpenTelemetry SDK writing its export into `DIR`
  (`--no-spans` for metrics only), to compare their CPU time with a run
  without; the agent loop's benchmark also checks the export it wrote. They
  configure the SDK as the telemetry page recommends at scale, and
  `--otel-sample`, `--otel-exemplars` and `--otel-interval` change it.
- An `otlp` extra and `flowgentic.otlp.configure()`, which streams an agent
  process's metrics and spans to an OpenTelemetry collector over OTLP/HTTP,
  for a live dashboard, beside the files that `read_export` reads or
  instead of them (`files=`). One call sets the process's meter and tracer
  providers with the telemetry page's configuration at scale (spans sampled
  per agent at a tenth, no exemplars), metrics every 5 s, and a resource
  that names the run (`service.namespace`), the process's role
  (`service.name`) and the process (`service.instance.id`).
  `OtlpTelemetry.rhapsody_options()` sends RHAPSODY's own metrics and spans
  to the same collector, through `Session.start_telemetry()` or AsyncFlow's
  `WorkflowEngine.start_telemetry()`. RHAPSODY's `start_telemetry()` leaves
  its session span current, so agents started in that context would all
  follow its sampling; the docs and the example start them in a context of
  their own. Metrics stay cumulative whatever the environment prefers.
  With the collector down, a shutdown takes about `timeout_s` per
  signal, RHAPSODY's span processor's included, and drops what is still
  queued. It refuses a process whose providers are set already, before it
  opens anything. Streaming costs what the files cost: +17.7 % of CPU for
  5,000 agents on the loop against +17.4 %, and one metrics export about
  1.5 ms.
- `examples/live-dashboard`: an OpenTelemetry collector, Prometheus, Jaeger
  and Grafana, started for the user who runs `stack.sh up`, on ports of that
  user's own, with a dashboard per component in a folder of its own:
  Flowgentic's (agents in each state, model calls and tokens per second,
  requests at the replicas, admission wait, time at the replica, requests
  sent again and outages, tools, event-loop lag and garbage collection) and
  RHAPSODY's (tasks and nodes). Grafana asks for a login and the collector
  for a token, both made by `stack.sh`, and only these two publish a port, on
  127.0.0.1. An application adds its own view through `APP_DASHBOARDS`. The
  agents-at-scale example streams to it with `--otlp`, and `--model-latency`
  makes its runs long enough to watch, on either runner. See the new Live
  dashboard page.
- `--otlp URL` for `benchmarks/agent_loop.py` and `benchmarks/agent_host.py`
  streams their export with `flowgentic.otlp`, and `benchmarks/otlp_sink.py`
  takes OTLP exports and keeps nothing, to measure the cost without a
  collector. CI installs the wheel with `agents` and `otlp` and runs the
  extra's tests, which stream to an OTLP receiver in the test, with no
  Docker.
- `benchmarks/agent_loop.py` runs the agents-at-scale example's workload in
  one agent process with the loop, the loop through LangChain's adapter, or
  `GraphRunner`; `benchmarks/client_cost.py` measures a model client's CPU per
  call against a local OpenAI-compatible stub.
- Contract tests read chat completions recorded from vLLM 0.19.1, beside
  completions shaped after SGLang's and OpenRouter's documented replies;
  `tests/unit/scale/completions/record.py` records a server's.
- Task tools on the application's AsyncFlow engine.
  `ToolRuntime(engine=flow, backend="compute")` runs each task tool as an
  AsyncFlow function task on a named backend of the engine the application
  passes in, so the tasks its agents ask for are tasks of the application.
  Suspension, repeat safety and the host's registrations work as on a RHAPSODY
  session. A tool may name its own backend (`task_tool(fn, backend="ai")`) and
  give the backend's own fields (`task_backend_specific_kwargs`, a dict). A
  sync function is called with no event loop running in its thread, as on a
  session. A task still running when the engine shuts down ends with
  `TaskCancelled`. A runtime takes a session or an engine, not both.
- `ToolRuntime(..., backend=...)`: the backend task tools run on unless a tool
  names its own, on a session as on an engine. A tool's `backend=None` names
  none.
- On an engine, a task belongs to the AsyncFlow workflow scope or block its
  agent runs in: AsyncFlow labels it with the scope's id, and cancelling the
  block cancels the task, which ends with `TaskCancelled`.
- On an engine, a task tool must be called on the engine's event loop, where
  AsyncFlow registers tasks; elsewhere, as inside an AsyncFlow task, it raises
  `RuntimeError`, which fails its agent. `ToolRuntime(engine=..., telemetry=...)`
  takes that engine's own telemetry only (`ValueError` otherwise), and an
  AsyncFlow without the scope variable the runtime reads is a `RuntimeError`
  that names the installed version.
- `ToolRuntime.check(tool)`, which `bind_tools` calls: a backend the session or
  engine lacks, or a field an engine cannot take, is a `ValueError` when the
  runtime is made or the tool is bound, and a `RuntimeError`, which fails the
  agent, when a tool that was not bound is called.
- The decision record. `TaskRecord` gains `uid` (the task's uid in AsyncFlow,
  or in RHAPSODY once the session has accepted it), `backend` (on a session,
  the one it sent the task to), `t_commit`
  (when the agent committed to the call), `t_start` and `t_finish`. With a
  model run through a fleet in an agent the host runs, `t_commit` is when the
  reply that made the call, by its id, reached the agent; otherwise it is
  `t_submit`. On an engine, `t_finish` is when the agent's process saw
  AsyncFlow resolve the task, and `t_start` comes from AsyncFlow's
  `TaskStarted` events in the telemetry the application passes
  (`ToolRuntime(engine=flow, telemetry=await flow.start_telemetry())`): when
  the backend reported the task running, which RHAPSODY's pools do when the
  pool gets it.

### Changed

- The fleet's API, as [What breaks](#what-breaks) says. A replica is a
  `Transport`; a LangChain chat model given as a replica, or returned by
  `Recovery`'s check, is wrapped in `ChatModelTransport`, so a fleet built
  from `ChatOpenAI` replicas keeps working, and its calls reach the models
  unchanged.
- The agents-at-scale example runs on `LoopRunner`, with its scripted model as
  a transport and its inline tool a plain function, and needs the `agents`
  extra only; `--runner graph` runs the LangGraph version,
  `campaign_langgraph.py`, with the `langgraph` extra. The guide, "Agents at
  scale", follows it, and LangGraph has a section of its own.
- The `dev` extra is `flowgentic[examples,agents,openai]` and the tools it
  needs.
- The fleet's `Recovery` also waits out the `openai` SDK's errors of a busy
  server, 408, 409 and 429, which the SDK retries itself unless given
  `max_retries=0`.
- `flowgentic.tools.ToolSetupError`, a `RuntimeError`, is what a runtime set
  up wrong raises: no session, engine or service client, a service client not
  started, a backend the session or engine lacks, a task tool off its
  engine's loop. The agent loop, and a graph's MCP tools, fail the agent on
  it only; any other error of a tool, a `RuntimeError` of its own included,
  reaches the model. An agent's error reads `ToolSetupError: ...` where it
  read `RuntimeError: ...`.
- On the agent loop, a sync inline tool runs in a thread, with the agent's
  context, as LangChain's `ToolNode` runs one; an inline tool that raises has
  an `execute_tool` span with status ERROR and its exception; tool results and
  arguments keep non-ASCII text; a `(role, content)` tuple input is one
  message; and tool calls in an input history run first only if nothing but
  tool results follows them.
- LangChain's adapter: `reply_from_message` reads the reasoning that
  `to_ai_message` writes, and `to_openai_messages` keeps an `AIMessage`'s
  invalid tool calls, with `{}` as arguments, so the tool messages answering
  them answer a call; `parse_completion` reads `reasoning_content` when
  `reasoning` is empty.
- `flowgentic.fleet.FleetChatModel` is no longer in the module's `__all__`, so
  `from flowgentic.fleet import *` works without LangChain; without it,
  naming `FleetChatModel` raises an `AttributeError` with the install hint.
  The example's `--runner graph` without LangGraph stops with that hint too.
- An agent host fails an agent whose runner suspends it 100 times in a row on
  the same tasks, all already ended: such a runner makes no progress, and was
  resumed at once, for ever. A suspended agent waits on the tasks it
  registered itself, before any other registration of the same key in the
  process, so two hosts in one process can run agents with the same ids.
- The `examples` extra, for the AsyncFlow path's demos, keeps LangGraph.
- The agent loop saves its turn's token with a suspended agent's state, so a
  runner that resumes an agent another runner suspended, on a shared runtime
  and store, finds its task; sync inline tools run on the runner's own
  threads (`runner.close()` ends them), and must be thread-safe; tool calls
  in an input history that a later message moved past get a result saying
  they were not run; text that UTF-8 cannot carry, a lone surrogate, goes as
  its escape.
- The transports: a given client left at httpx's default 5 s gets 600 s, as
  with the SDK, and `transport.timeout` is the timeout calls get; `aclose()`,
  on both, refuses new calls and returns at once, the clients closing when
  their calls end; an error object with status 200 is read by its `code`, so
  OpenRouter's 429 and 502 are transient; a header with a line break or
  non-ASCII text is a non-transient error; a 401 without a key says where
  `OPENAI_API_KEY` goes.
- The `asyncflow` extra is `radical-asyncflow>=0.7,<0.8` (was `>=0.6,<0.7`).
  AsyncFlow 0.7 sets a task's uid when the task is called, which the decision
  record needs, and its `task` spans name the backend each task ran on, where
  0.6 labelled every one with the default backend.
- The error of a task tool on a runtime with neither a session nor an engine
  now reads "task tools need a ToolRuntime with a RHAPSODY session or an
  AsyncFlow engine".
- `TaskRecord.t_submit` is taken when `ToolRuntime.submit` gets the call, not
  when the task's own coroutine first runs.
- On a session, a task tool naming a backend the session lacks fails its agent
  with `RuntimeError` when called, and `bind_tools` refuses it; before, the
  session's error reached the model.
- `flowgentic.tools.TaskCancelled` is now defined in `flowgentic._agents`, so
  that neither seam imports the other; import it from `flowgentic.tools`, as
  before.


## [0.5.1] - 2026-11-19

A patch release: fixes for most of the known limitations that 0.5.0's
documentation lists, and for defects found since. The entries under Changed
change behaviour a caller may rely on. A tool call's key and a task's uid
differ from 0.5.0's, so upgrade between campaigns, not during one.

### Added

- `GraphRunner(graph, delete_finished=True)` deletes a finished agent's thread
  from the graph's checkpointer once its run ends, before the host passes the
  output to `on_done`, so the store holds the agents still running, not every
  agent of the campaign. Turn it on when the store is a Dragon dictionary,
  whose size is fixed. Interrupted, failed and cancelled agents keep their
  threads. A thread that cannot be deleted is logged and counted in
  `GraphRunner.delete_failures`, and the agent still finishes. With it on,
  `on_done` can no longer read the thread's state (`graph.get_state` finds it
  empty), and an agent id run again, as a conversation continued in a later
  host run, starts a fresh thread. It is off by default, which keeps 0.5.0's
  behaviour. The agents-at-scale example turns it on.
- `flowgentic.tools.TaskCancelled`, the error a task ends with when RHAPSODY
  cancels it or its session closes before it ends.
- `flowgentic.fleet`: an `AdmissionGate` counts the calls it admits alone
  above its token cap (`oversize` in its stats), and the fleet logs the
  first as a warning, once for all its replicas; a gate built on its own
  warns once on its own. A gate holds one process's share of a replica's
  caps, so one such call takes the replica past its cap while the other
  processes use their shares.
- The package declares its classifiers and project URLs, and ships a
  `py.typed` marker, so type checkers read Flowgentic's annotations. Its
  description, and the documentation site's, now say what Flowgentic is.

### Changed

- `KeyValueSaver` keeps a thread's list of subgraph namespaces as a mapping,
  each namespace with the parent checkpoint it ran under. This version reads a
  store written by 0.5.0. The other way round, 0.5.0's saver can read, resume
  and delete a thread this version saved a subgraph in, but it raises
  `AttributeError` when it saves a new subgraph namespace in that thread. Do
  not go back to 0.5.0 over a store with such threads.
- A task or MCP call made by a LangGraph agent is keyed by its agent, the
  LangGraph task that runs the tool, and the tool call's id:
  `agent/task/call`, where it was `agent/call`. LangGraph gives a task the
  same id when it runs again on resume, so a repeated call still runs
  nothing; outside a LangGraph run the key is `agent/call`, as before.
  `ToolRuntime.key`, `submit` and `call_service` take the task as a
  keyword-only `scope`. A `/` or `%` in an id is written `%2F` or `%25` in
  the key.
- A task's RHAPSODY uid escapes its key one-to-one: `/` is written `.`, `_`
  is written `__`, and any character other than a letter, a digit or `-` is
  written `_x<hex>_`, where every such character was `_`. A uid made by 0.5.0
  for the same call differs.
- Cancelling a task tool's wait, as cancelling its agent does, leaves a task
  that has started running, where it cancelled Flowgentic's side of the task.
  The task keeps its place in its `TaskOrder` until it ends, and the same call
  made again gets its result. A task that its order has not admitted yet is
  withdrawn once no call waits for it, the host's wait for a suspended agent
  included: it never reaches RHAPSODY, and the call made again submits it
  afresh. A task cancelled before admission is likewise forgotten.
- `ToolServiceClient` takes the service's `call_timeout` (120 s by default, as
  `serve`'s), and its deadline, `timeout`, defaults to `call_timeout` plus
  30 s: 150 s by default, as before. A `timeout` not above `call_timeout`
  raises `ValueError`; pass the service's `call_timeout` with it.
- `flowgentic.fleet`: a `CallRecord`'s times add up to the call's.
  `wait_s` is the wait for admission alone, summed over the call's tries;
  before, it ran from the call's start to its last admission, so it counted
  an outage that `Recovery` waited out. The new `recovery_s` holds that
  outage: the tries that failed and the wait for the replica to come back.
  `service_s` is unchanged.
- `flowgentic.fleet`: the admission-wait and request-duration histograms are
  recorded for each try, so a retried call records one of each per try. A
  try that fails, also one that ends the call, records its time at the
  replica with `error.type`; before, a failed call recorded nothing.
- `flowgentic.fleet`: a routing reads a replica's load only when it asks for
  it. Under `AgentAffinity` and `RoundRobin` a call no longer reads every
  replica's load: with 256 replicas, a call costs what it costs with one.
- `flowgentic.agent.time` is a counter, as agent-seconds add up. It was a
  gauge that started from 0 at each run of a host and lost a host's time when
  the host was gone. It now adds up the agent-seconds of every run in the
  process, and an exporter reads it as a monotonic sum. A run's own
  agent-seconds stay in its report.

### Fixed

- `KeyValueSaver` drops a subgraph's checkpoints once its parent has moved past
  the step that ran it, with the subgraph's own subgraphs. A graph that runs a
  suspending subgraph in a loop, as a supervisor does, no longer stores more
  with every turn. A stateful subgraph's checkpoints (`checkpointer=True`),
  which every call reuses, are kept.
- `KeyValueSaver.stats()` counts what LangGraph's copies of the saver put and
  read. With `LANGGRAPH_STRICT_MSGPACK=true`, a graph runs on a copy, and the
  saver reported zeros.
- `from flowgentic.langgraph import *` works without the `asyncflow` extra.
- `GraphRunner` no longer keeps a resume count for an agent that the host
  cancels or fails while it waits between two resumes.
- A model server that numbers its tool calls per message reuses call ids on
  later turns, and the later, different call got the first call's result. It
  now runs, when each turn's tools run in a LangGraph task of their own; a
  node that loops between the model and its tools itself still shares one key
  across turns.
- Two calls whose keys differ only in characters other than letters, digits,
  `_`, `.` and `-`, such as `a.b/c` and `a/b.c`, shared one RHAPSODY uid, and
  RHAPSODY answered both with one task.
- A task tool's call made again after its wait was cancelled raised a bare
  `CancelledError`, which failed its agent, and a cancelled wait released its
  task's place in its `TaskOrder` while the task still ran. A task cancelled by
  whoever holds its future now reaches the model as a failed call.
- A RHAPSODY session that closed left the tasks it dropped unfinished on the
  thread-pool backend: their places in their `TaskOrder` were never given back,
  and calls still waiting on them waited forever. They now end with
  `TaskCancelled`, and a task admitted after its session closed fails at once.
- A task that RHAPSODY cancelled was taken for a success that returned `None`.
  It now ends with `TaskCancelled`.
- A RHAPSODY session keeps every task's description for its life, and each
  held the task's arguments, its result, the backend's text of the result and
  its exception, and through the exception's traceback the runtime that ran it.
  Flowgentic now drops them once it has read the task's outcome: 20,000 tasks
  with 10 kB results, on a thread pool, took 101 MB instead of 465 MB.
  RHAPSODY 0.7 still keeps each task's description.
- A task that failed with no call left to read its outcome, as when its agent
  was stopped, was logged by asyncio as an exception never retrieved.
- `flowgentic.fleet`: an agent's first call is estimated as a fixed part,
  such as tool schemas the messages do not show, plus tokens per character,
  both learned from earlier first calls. With tokens per character alone,
  short first prompts taught a ratio that made a later long first prompt's
  estimate many times too large, and its call waited to run alone. The
  tokens per character are never fewer than those of the characters later
  calls add, so a first call is never estimated below its text alone.
  `stats()["tokens_per_char"]` gains `first_call_base`, the fixed part.
- `flowgentic.fleet`: a call that joined a recovery watch started for an
  earlier call failed when that watch gave up, before its own `outage_s`
  had passed. It now starts a new watch if at least `poll_s` of its own
  `outage_s` is left, and fails then otherwise.
- `Fleet` refuses a negative `reply_reserve`, which took tokens off every
  call's admission estimate.
- The `flowgentic.agents` gauge counts the agents of runs in progress only.
  It counted the agents of every run that had ended too, for as long as its
  host was alive: a cancelled run's agents stayed `pending`, and finished
  ones `done`. With no run in progress, every state reads 0.
- `Collector` refuses a `threshold` below 1. CPython takes a first threshold
  of 0 to turn automatic collection off, so `Collector(threshold=0)` stopped
  the cyclic collector for as long as the host ran; below 0 it collected at
  nearly every allocation.
- `register_pending` called outside an agent's task, as by code that submits
  tasks before a run, no longer keeps the future for the life of the process.
  The first agent that waits on such a registration takes it, and it is
  released when that agent ends. One that no agent takes is released when the
  host run ends, or the last of the runs in progress if several overlap. A
  registration made outside an agent with no host run after it is still held
  for the life of the process. A registration made in an agent's task lasts
  until the agent ends, as before.

## [0.5.0] - 2026-11-18

Flowgentic runs LLM agents on HPC resources with the RADICAL stack: as
AsyncFlow workflow tasks, and, from this release, as LangGraph agents at scale
whose tools run as RHAPSODY tasks and whose state lives in RaDEX. 0.5.0 adds
that second path: an agent host that runs many thousands of agents per
process, a checkpointer over a key-value store, tool placement with a shared
MCP tool service, and a client for a fleet of model servers. The path is in
active development; the AsyncFlow path stays in maintenance mode.

**Breaking changes.**

- Flowgentic requires Python 3.11 or later, as RHAPSODY, AsyncFlow 0.7 and
  Dragon do. To stay on Python 3.10, stay on 0.4.
- The dependencies are split by path. A plain install brings only pydantic and
  the OpenTelemetry API. Install the extras you use: `langgraph` (LangGraph and
  LangChain) and `asyncflow` (RADICAL AsyncFlow). The LangGraph integration
  needs both, `bind_agent` needs `asyncflow`, and
  `flowgentic[langgraph,asyncflow]` installs what 0.4 installed.
- `flowgentic.langGraph` is renamed `flowgentic.langgraph`. Change the imports.
  A source checkout updated in place from 0.4 on a case-insensitive file
  system, as macOS's is by default, can keep the directory's old name, because
  its ignored `__pycache__` keeps the directory, and `import
  flowgentic.langgraph` then fails. Run `git clean -fdX src` before updating;
  after, remove `src/flowgentic/langGraph` and run `git checkout -- src`. An
  installed wheel upgrades cleanly.
- `LangraphIntegration` and `ExecutionWrappersLangraph` are renamed
  `LangGraphIntegration` and `ExecutionWrappersLangGraph`, with no alias.
  Change the imports.

### Added

- `examples/agents-at-scale/`: a campaign of LangGraph agents on a laptop, in
  one agent process or several, with a scripted model behind a fleet of two
  replicas, RHAPSODY on a thread pool, the in-memory store, the tool service
  and an MCP server on 127.0.0.1. CI runs it in one process and in two
  processes that share one tool service. The guide
  [LangGraph agents at scale](https://stride-research.github.io/flowgentic/guides/agents-at-scale/)
  walks through it, and through choosing a process's limits from the host's
  report. With `--dragon`, under `dragon`, the same campaign runs on Dragon:
  the agents' state in RaDEX, the simulations on RHAPSODY's native Dragon
  backend, and the tool service and each agent process in a Dragon process.
- A Dragon suite: tests marked `dragon` run the store's contract on RaDEX,
  agents suspended by one Dragon process and resumed by another, the tool
  service in a Dragon process with two agent processes over Dragon's queues,
  and the example with `--dragon`. CI skips them; they run by hand under
  `dragon` (see CONTRIBUTING.md).
- `flowgentic.host`: an agent host that runs many agents in one process,
  through a runner per framework. While it runs it raises the cyclic
  collector's first threshold and freezes the objects alive once agents have
  started, and when the last host that froze ends it unfreezes every frozen
  object, so no reference cycle stays frozen; it caps the agents live at
  once, staggers the first wave's starts, resumes agents that wait on tasks,
  and counts agents by state, in three categories: working, waiting, and
  making a model request. Its report gives each agent's outcome, the
  agent-seconds by state, the event loop's lag and the collector's pauses.
  Each agent's run is an `invoke_agent` span, with status ERROR and the
  exception when the agent fails, and the agents by state, the loop lag and
  the collector's pauses are OpenTelemetry metrics. Hosts that run at the
  same time record each lag sample and each pause once, and the pauses are
  recorded from the event loop, never inside a collection, where an SDK's
  locks could deadlock the process.
- Flowgentic's histograms, all in seconds, advise the SDK of bucket
  boundaries sized for what each measures, from the collector's pauses to
  tool calls of an hour, in place of the SDK's default boundaries, which suit
  milliseconds. The Telemetry page lists every metric and shows how to
  configure a meter provider.
- `AgentHost.run_indexed(indices, input_for, id_prefix=...)` runs a range of
  agents named by a prefix and an integer, and builds each agent's input as a
  slot opens, so a process's memory does not grow with the campaign's size.
  Its report keeps counts, not per-agent results; `on_done` receives each
  result.
- `HostReport.outcomes` counts the agents of a run by status.
- `flowgentic.host.register_pending`, with which a tool records the future of
  a task it submitted under the task's key, for the host to await when the
  agent suspends.
- `flowgentic.langgraph.GraphRunner`, the host's runner for LangGraph graphs,
  and `route_tool_calls`, which sends each tool call to the tool node as its
  own task, so that an agent resuming from a suspension runs no other tool
  call again. A suspended run is freed as soon as it stops.
- `GraphRunner` lets one agent resume at most `recursion_limit` times, and
  fails it with `GraphRecursionError` after that: each resume is a new
  LangGraph run, which counts its steps afresh, so the limit alone would never
  stop an agent that suspends on every turn. It refuses a graph compiled with
  `interrupt_before` or `interrupt_after`, whose static breakpoints would end
  an agent as finished, with partial output.
- `flowgentic.langgraph.KeyValueSaver`, a LangGraph checkpointer that keeps
  agents' state in a key-value store, and `flowgentic.store` with two stores:
  RaDEX on a Dragon distributed dictionary, and a dictionary in the process.
  The saver keeps each thread's newest checkpoint by default (`keep_last`).
  Pending writes survive a retried save, a saver reopened over the same store
  and other savers in the process, and delta channels keep the history they
  replay after older checkpoints are dropped. A suspension costs six store
  calls. The `langgraph` extra now names `langgraph-checkpoint`, which
  LangGraph already brought.
- A tool service, `flowgentic.tools.service`: one process that calls MCP tools
  for many agent processes over queues, and answers a repeated call once. Its
  MCP client, `flowgentic.tools.mcp.SupervisedMcpPool`, keeps a failed
  connection from cancelling other calls. The pool needs the new `mcp` extra.
  Calls are not resent by default: a call that lost its connection is sent
  again only when the caller passes `idempotent=True`, as a lookup can. A
  failure that left the call safe to repeat is not kept, so asking again with
  the same uid calls the tool again.
- A tool service call has a deadline: with no answer within the client's
  `timeout` (150 s by default) it fails with `ToolServiceError`, and with
  `alive`, a check that the service still runs (such as a test of its pid),
  every waiting call fails as soon as the service has stopped. A call's
  arguments travel as JSON values, so the service needs none of the agents'
  classes, and arguments with no JSON form fail their own call. The service
  refuses a request from a process with no reply queue, and a reply queue that
  fails loses no other process's answers. An MCP success with no content is a
  success with no data, and a result with several text blocks returns all of
  them.
- Tool placement, `flowgentic.tools`: a tool runs inline, as a RHAPSODY task
  (`task_tool`) or through the tool service (`mcp_tool`), and
  `flowgentic.langgraph.bind_tools` turns these into LangChain tools. A task
  tool may suspend its agent while its task runs. A call made again with the
  same agent and call id gets the first call's result, so an agent that
  resumes runs no task twice. A `TaskOrder` decides when tasks reach RHAPSODY,
  or refuses them. Each task or service call is an `execute_tool` span and a
  point of the `flowgentic.tool.duration` histogram. Task tools need the new
  `rhapsody` extra.
- A task tool's function may be `async def`: the worker runs it to its end,
  as RHAPSODY does with a coroutine function.
- `flowgentic.fleet`: a client for a fleet of model replicas. It keeps each
  agent on one replica, so the agent's prompt stays in that replica's prefix
  cache; admits calls under caps on each replica's tokens of live context and
  requests in flight, shared among agent processes; waits out a replica whose
  server went away, for at most `outage_s` per call, treating a health check
  that raises or does not answer within `check_timeout_s` as the replica
  still being down; and records each call. Per call it passes settings to the
  replica, adds a `reserve` to the admission estimate, such as a thinking
  budget, and carries the caller's `meta` into the call's record; reasoning
  tokens are recorded and left out of the agent's size. Other admission
  policies plug in as an `Admission` whose gates follow the `Gate` protocol:
  a gate is told which agent's call it admits, and `Fleet.agent_done` tells it
  that an agent ended. `Fleet.chat_model()`, for prebuilt agents, routes by the
  `thread_id` of the config it is given or of the LangGraph run it is called
  in, and opens no LangChain run of its own, so callbacks and tracers count
  each call once. The admission wait, the time at the replica and each
  replica's load are OpenTelemetry metrics. It needs the `langgraph` extra.

### Changed

- The status page, home page, README and architecture page describe both
  paths: agents at scale, in active development, and the AsyncFlow path, in
  maintenance mode.
- The `examples` extra moves to RHAPSODY 0.7 (`rhapsody-py[telemetry]>=0.7,<0.8`).
- `flowgentic.langgraph` imports `LangGraphIntegration` when first used, so
  `GraphRunner` needs only the `langgraph` extra.
- Importing a part whose extra is not installed raises `ModuleNotFoundError`
  with the extra to install.
- CI runs the test suite on Python 3.11, 3.12 and 3.13, and on 3.11 with the
  lowest versions the ranges allow, and records the tested versions of
  RHAPSODY, the MCP SDK and LangGraph's checkpointer with the others. The
  documentation's cap on `pymdown-extensions`, needed only on Python 3.10, is
  lifted.

## [0.4.0] - 2026-10-30

A repair release. It removes features that 0.3 shipped broken or unsafe, or
that did not serve Flowgentic's purpose: running agent decisions as workflow
tasks, checking their outputs and tracing them with the tasks. It adds
acceptance of agent outputs and OpenTelemetry tracing.

**Breaking changes.** Each is detailed below. Where 0.3's behaviour can be
kept, the entry says how.

- `import flowgentic` no longer configures logging. To print Flowgentic's
  records, call `flowgentic.configure_logging()`.
- The logging utilities (`flowgentic.utils.Logger`, its formatters and
  filter, `add_context_to_log`) and the settings file (`config.yml`,
  `FLOWGENTIC_CONFIG`, `flowgentic.settings`) are gone. Configure logging in
  the application, or call `flowgentic.configure_logging()`.
- Each decorated call runs once: the retry loop,
  `flowgentic.langGraph.fault_tolerance` and the decorator's `retry=` are
  gone, and an unknown keyword raises `TypeError`. The nearest to 0.3's
  default of 3 attempts of at most 30 s each is, on a graph node,
  `add_node(..., retry_policy=RetryPolicy(max_attempts=3), timeout=30)`, where
  `timeout` needs LangGraph 1.2 or later. LangGraph's default `retry_on`
  retries `ConnectionError` and an attempt that timed out, but not the other
  `OSError`s that 0.3 retried: name the transient ones in `retry_on`. On a
  model call, the chat model's `max_retries` and `timeout` take its place; on
  an AsyncFlow task, a wrapper in the application.
- `flowgentic.academy` is gone, and Flowgentic no longer depends on
  academy-py. Call Academy agents from AsyncFlow tasks directly, with
  `academy-py>=1.0,<2` installed, as
  [Academy actions as AsyncFlow tasks](https://stride-research.github.io/flowgentic/guides/academy-on-asyncflow/)
  shows.
- The node report is gone: `flowgentic.utils.telemetry`,
  `LangraphIntegration.agent_introspector`, `generate_execution_artifacts` and
  `LangraphUtils.render_graph`. Export the run's spans to a trace backend or
  a JSON-lines file instead, and draw a graph with LangGraph's
  `app.get_graph().draw_png(path)`.
- `flowgentic.langGraph.memory`, the integration's `memory_manager` and
  `flowgentic.langGraph.mutable_graph` are gone, with two attributes of
  `ExecutionWrappersLangraph` that nothing used. Bound an agent's history with
  LangChain's `SummarizationMiddleware`, `ContextEditingMiddleware` or
  `trim_messages`, and build a new `StateGraph` to change a graph between
  runs.

### Added

- OpenTelemetry spans that follow OpenTelemetry's GenAI semantic conventions,
  so an agent's decisions, the AsyncFlow tasks they run in and the model calls
  inside them form one trace:
  - Each call of an agent bound with `bind_agent` is an
    `invoke_agent <identity>` span, with the AsyncFlow task's uid and backend
    (`flowgentic.task.uid`, `flowgentic.task.backend`).
  - Every task and service Flowgentic submits runs in its caller's trace
    context. AsyncFlow runs a task's function in a pool thread or another
    process, where a model call used to start a trace of its own.
  - Model calls, tools and graphs are recorded by OpenTelemetry's GenAI
    instrumentation, and tasks by RHAPSODY, through `flow.start_telemetry()`.

  Flowgentic depends on `opentelemetry-api>=1.43,<2` only: without the SDK,
  its spans are no-ops. The `examples` extra adds the SDK, the GenAI
  instrumentation for LangChain and OpenAI, and `rhapsody-py[telemetry]`. See
  [Telemetry](https://stride-research.github.io/flowgentic/features/telemetry/).
- The AI-HPC demo's runners take `--trace PATH`, which writes the run's spans
  and events to `PATH`, one JSON object per line. `examples/telemetry/cost.py`
  computes cost from such a file in three ways: the cost a provider reported,
  when a span carries one; usage times a price, for a remote model; and a share
  of a deployment's GPU-hours, for a model on the allocation.
- `flowgentic.acceptance`, which accepts or rejects an agent's output on
  explicit criteria, for runs with no human in the loop.
  `accept(propose, *, checks=(), attempts=1, fallback=None, name=None)`:
  - asks the producer for a proposal, and runs the checks on it in order,
    stopping at the first rejection;
  - asks again, within `attempts`, handing the producer every earlier attempt
    so that it can build the re-ask;
  - returns an `Outcome` with the value, the disposition (`accepted` or
    `fallback`) and every attempt, or raises `NotAccepted` with the attempts
    when nothing passed and there is no fallback.

  A check or the producer rejects an output with `Rejected(reason, check=...)`
  or a Pydantic `ValidationError`; any other exception propagates at once, with
  no further attempt. Each call is an `accept <name>` span with one
  `accept.attempt <name>` span per attempt, and each check that ran emits a
  `gen_ai.evaluation.result` event through OpenTelemetry's Logs API. A rejected
  value is recorded only when OpenTelemetry's GenAI content capture puts
  content on spans. See
  [Acceptance](https://stride-research.github.io/flowgentic/features/acceptance/).
- The agentic AI-HPC demo's planner passes `accept`: three checks (the batch
  size, the bounds, distinct candidates), one re-ask with the reasons, and the
  surrogate's batch as the fallback. Its record of each decision holds the
  disposition and every rejected proposal, and the runner prints their count.
  The version without Flowgentic writes the same policy by hand, and the
  substitution tests check that both reach the same decisions on a re-ask and
  on a fallback.

### Changed

- **Breaking:** `import flowgentic` configures nothing. It used to set the root
  logger to DEBUG, add a handler that printed every record to stdout, start a
  thread and register an `atexit` hook; in test runs, that handler wrote to
  streams the runner had closed. The `flowgentic` logger now has no handler:
  without configuration, warnings and errors reach stderr through Python's
  last-resort handler, and the rest is the application's. To get Flowgentic's
  records back, call `flowgentic.configure_logging()`, which prints them in
  plain text to stderr; see
  [Logging](https://stride-research.github.io/flowgentic/features/logging/).
- Log records follow a level contract: DEBUG for per-call detail, INFO for
  lifecycle events, WARNING for degraded results and refused requests, ERROR
  for failures. Per-call records that were INFO are DEBUG, such as the
  decorator's "Registering function … as AsyncFlow …". No record carries
  prompts, model outputs, message lists or graph states, at any level. Records
  use lazy `%`-style arguments, and CI checks it.
- The AI-HPC demo's decision model keeps the model's raw message
  (`with_structured_output(..., include_raw=True)`), and rejects an answer it
  cannot use with `Rejected`: one that does not parse, a refusal, or one cut
  off at the token limit. Its messages no longer carry token counts set to
  zero, which claimed a measurement nobody made.
- The documentation site follows the latest release: it deploys when the
  repository's latest GitHub release is published (not a pre-release, nor a
  patch to an older line), or by hand for a given release tag, no longer on
  every push to `main`. It stops describing changes that no release contains.
- CI runs three design-pattern examples, `sequential/research_agent`,
  `supervisor/toy` and `supervisor/product_research`, with a scripted chat
  model in place of
  `ChatLLMProvider`, and checks that each completes.
  They need OpenRouter credentials, so CI had never run them. Live runs stay
  opt-in: `make tests-integration-llm` locally, or the LLM examples workflow,
  which a maintainer starts by hand on GitHub with the repository secret
  `OPEN_ROUTER_API_KEY`.
- Dependencies are ranges, not pins: `radical-asyncflow>=0.6,<0.7`, where 0.3
  required `==0.6.0`, because before 1.0 an AsyncFlow minor release may break;
  `langchain-openai` and `langchain-ollama` at `>=1.0,<2.0`, like the other
  LangChain packages. The `examples` and `dev` extras have floors, and `dev`
  keeps `pymdown-extensions` below 12.2: 12.2 breaks mkdocstrings before 1.0.7,
  and 1.0.7 needs Python 3.11. CI runs the suite on the lowest versions the
  ranges allow. A weekly workflow runs the suite and the docs build on the
  newest releases within them, and the suite on the newest AsyncFlow and Academy
  releases beyond them. Dependabot updates `uv.lock` within the ranges, never
  the ranges themselves, and the workflows' actions.

### Removed

Nobody uses Flowgentic yet, so these go without a deprecation period.

- **Breaking:** the retry loop around every decorated call, with
  `flowgentic.langGraph.fault_tolerance` (`RetryConfig`,
  `LangraphToolFaultTolerance`) and the `asyncflow` decorator's `retry=`
  argument. Every tool, task, service and graph node ran in it, by default
  with 3 attempts of at most 30 s each, retrying `OSError` among other errors.
  On graph nodes it re-ran finished scientific work: with a simulation that
  kept failing, it made 24 attempts where the application's own policy made 8,
  and it cancelled and re-ran a node whose work took longer than 30 s until
  the cycle failed
  ([substitution experiment](https://stride-research.github.io/flowgentic/evaluation/substitution/),
  row 3). Each call now runs once. A model call's retries and deadline belong
  to the chat model (`max_retries`, `timeout`), a graph node's to LangGraph
  (`add_node(..., retry_policy=..., timeout=...)`), and a task's to the
  application until AsyncFlow has per-task retry, as in the AI-HPC demos. See
  [Retries and timeouts](https://stride-research.github.io/flowgentic/guides/retries-and-timeouts/).
  The decorator's keyword arguments are now explicit, so an unknown one, such
  as `retry=`, raises `TypeError`; `tool_description` was the only other.
- **Breaking:** `flowgentic.academy` (`AcademyIntegration`) and its two
  examples. In 0.3 it could not run an Academy action through AsyncFlow: every
  call failed with `ExchangeClientNotFoundError`, and the examples failed at
  import. Repaired and then compared with direct Academy calls in AsyncFlow
  tasks, it added no capability: the same results, tasks and agent lifecycle,
  for 6 fewer lines of application code
  ([Academy substitution check](https://stride-research.github.io/flowgentic/evaluation/academy/)).
  The guide [Academy actions as AsyncFlow tasks](https://stride-research.github.io/flowgentic/guides/academy-on-asyncflow/)
  now shows the direct calls, with a tested example. Placing Academy agents
  on HPC resources belongs to RADICAL's execution layer, and an agent that
  drives the workflow to ADR. Flowgentic no longer depends on academy-py; the
  `examples` extra brings `academy-py>=1.0,<2` for the example.
- **Breaking:** the settings file: `config.yml`, looked up at import in the
  working directory, `~/.flowgentic/`, the file named by `FLOWGENTIC_CONFIG`
  and the source tree, and the `flowgentic.settings` module. An unrelated
  `config.yml` in the working directory made `import flowgentic` fail with
  `KeyError`, and the file held only the log level, now the application's, and
  the report's paths, which went with the report.
- **Breaking:** `flowgentic.utils.Logger`, its colored and JSON formatters,
  the base64 redaction filter and `flowgentic.utils.add_context_to_log`. Use
  `flowgentic.configure_logging()` or the application's own logging
  configuration; OpenTelemetry's logging instrumentation adds trace and span
  ids to every record.
- The `pyyaml` and `python-json-logger` dependencies.
- **Breaking:** the node report:
  - `flowgentic.utils.telemetry` (`GraphIntrospector`, its extractor, schemas
    and report generator);
  - `LangraphIntegration.agent_introspector` and `generate_execution_artifacts`;
  - `LangraphUtils` and its `render_graph`.

  The report rebuilt each node's record from the graph's final messages after
  the run. So it saw no tasks, retries or rejected proposals, and it summed
  token counts that the demo had set to zero. The spans replace it; any
  trace backend that reads OTLP shows them, and so does a JSON-lines file. To
  draw a graph, call LangGraph's `app.get_graph().draw_png(path)`. The
  design-pattern examples' committed `agent_execution_results/` files are gone,
  and the examples' entry points return the final state.
- **Breaking:** the memory manager and the mutable graph. Neither runs agent
  decisions or model calls as workflow tasks, checks their outputs or traces
  them, and LangChain and LangGraph cover both:
  - `flowgentic.langGraph.memory` (`MemoryConfig`, `MemoryManager`,
    `LangraphMemoryManager`, `MemoryEnabledState`), the integration's
    `memory_manager`, and the example `sequential/research_agent_memory`. Its
    trimming ignored the pairing of a tool call with its result, so every
    strategy could return a conversation that a chat API rejects: `trim_last`
    could keep a tool result whose call it had dropped, and
    `importance_based` reordered the messages by score. `"summarize"` made a
    blocking model call on the event loop, and five parameters had no effect.
    For an agent built with `create_agent`, `SummarizationMiddleware` and
    `ContextEditingMiddleware` from `langchain.agents.middleware` bound the
    history and keep each tool call with its result; in a graph of one's own,
    `langchain_core.messages.trim_messages` does.
  - `flowgentic.langGraph.mutable_graph` (`MutableGraph`) and its example. It
    changed no running graph: each edit recompiled a new sequential
    `StateGraph` from a list of node names, and refused edits returned
    `False`. Within a run, LangGraph routes with conditional edges, `Send` and
    `Command(goto=...)`; between runs, build a new `StateGraph`.
  - `ExecutionWrappersLangraph.react_agents` and `service_registry`, which
    nothing used.

### Fixed

- `langgraph` 1.1.5 or later is required; 0.3 allowed 1.0. langgraph 1.0 to
  1.1.4 install with the newest `langgraph-prebuilt` 1.0.x, which imports
  names that langgraph 1.1.5 added, so importing Flowgentic's LangGraph
  integration failed with `ImportError`.
- The `supervisor/toy` example hid the error of a failed run: when the graph
  raised, the `finally` block that writes the report read a result that was
  never set, and the run ended in `UnboundLocalError`. The graph's own error
  now propagates.
- The `sequential/research_agent` example exited 0 when a stage failed: its
  nodes route failures to the error handler, so a live run in which every
  model call failed, for want of a key or in an outage, passed
  `tests/integration/test_integration.py`, which checks only the exit code.
  A run that does not complete now raises `RuntimeError` with the stages'
  errors.

## [0.3.1] - 2026-10-23

A patch release: fixes for the defects that 0.3.0's documentation lists.

### Fixed

- Memory, `trim_middle`: with one free place beside the system messages, every
  message was kept. The strategy now fills every free place, and the most recent
  messages take the odd place of an odd count.
- Memory, `importance_based`: after the first trim, each message was scored by
  another message's importance. The scores now follow the trimmed history, which
  also corrects the importance weighting in `get_relevant_context`.
- Memory, `summarize`: the summary could take the history one message over the
  limit. It now fits within the limit. With one free place, or when no summary
  is made, the strategy trims as `trim_last` does; a failed summary used to
  leave its place empty, or keep the oldest messages.
- `MutableGraph.update_graph` returns `False` for a replacement node that is
  already active. It used to list the node twice, and the rebuild raised
  LangGraph's `ValueError`.
- `generate_execution_artifacts(generate_report=...)` was inverted: `True`
  skipped the report. `True` now writes it and `False` skips it, and the default
  becomes `True`, so a call without the flag still writes the report.
- Telemetry: tool calls are read from `AIMessage.tool_calls`, and malformed ones
  from `invalid_tool_calls`. Only OpenAI-format `additional_kwargs` were read,
  so other models' tool calls were missing from the report.
- Telemetry: an AI message's text is recorded as the node's final response even
  when the message has no `additional_kwargs`, and list content is joined into
  text.

## [0.3.0] - 2026-10-22

A consolidation release. It preserves the work since 0.2.0 as a supported,
documented snapshot: fixes and evidence, no new features beyond those already
on `main`.

### Added

- `flowgentic.agent.bind_agent`: binds a framework-specific agent component to
  an AsyncFlow task on a named backend; each call runs the component as that
  task and returns its structured decision with the agent's identity and
  timing.
- Named AsyncFlow backends: Flowgentic's `asyncflow` decorator takes `backend=`
  and passes it to AsyncFlow, except for `EXECUTION_BLOCK`.
- Service tasks (`AsyncFlowType.SERVICE_TASK`) for resident AsyncFlow
  services, and `MutableGraph` (`flowgentic.langGraph.mutable_graph`) for
  graphs changed at runtime.
- AI-HPC coupling demos in `examples/ai-hpc-coupling/`: a RADICAL-only
  baseline, a deterministic LangGraph version, an agentic version and an
  agent-augmented version, with offline rehearsal decisions and optional ADR
  control.
- `--integration direct` for the agentic demo (`direct_agentic_application.py`)
  runs the same agent graph on AsyncFlow without Flowgentic, and the run's
  `campaign_summary.json` records which integration ran. The substitution
  experiment compares the two (`tests/integration/test_substitution.py`,
  `docs/evaluation/substitution.md`), including LLM tool selection and an agent
  handoff driven by a scripted chat model.
- An `examples` extra (`python-dotenv`), included in `dev`.
- CI: a strict MkDocs build job, and Python 3.12 in the test matrix.
- Tests that run the offline mutable-graph and service-task examples
  (`tests/integration/test_feature_examples.py`).
- This changelog.

### Changed

- Breaking: the decorator API moved from
  `integration.agents.asyncflow(flow_type=TOOL | NODE | UTISL_TASK | BLOCK)` to
  `integration.execution_wrappers.asyncflow(flow_type=AGENT_TOOL_AS_FUNCTION |
  FUNCTION_TASK | SERVICE_TASK | EXECUTION_BLOCK)`. Execution reports moved from
  `integration.agent_logger` to `integration.agent_introspector`
  (`flowgentic.utils.telemetry`).
- Python 3.10 or later is required (0.2.0 allowed 3.9). CI tests 3.10, 3.11
  and 3.12.
- Dependencies are released versions:
  - `radical-asyncflow==0.6.0` and `academy-py==0.3.0` come from PyPI instead
    of git URLs.
  - LangGraph, langchain-core and langchain move to 1.x
    (`>=1.0,<2.0`); the lock tests LangGraph 1.2.16 and langchain-core 1.6.13.
  - `python-json-logger>=3.1.0`.
  - `pyyaml` is declared.
- `python-dotenv` moved from the core dependencies to the `examples` extra.
- LangGraph 1.x's `ToolNode` re-raises tool errors by default, where 0.6
  returned them to the model as messages. A Flowgentic tool that still fails
  after its retries now aborts the graph run; pass `handle_tool_errors=True` to
  `ToolNode` to keep the 0.6 behaviour.
- The LangGraph examples build their agents with `langchain.agents.create_agent`,
  which replaces LangGraph's deprecated `create_react_agent`.
- The AI-HPC demo launchers use the installed AsyncFlow, and import a local
  checkout only when `ASYNCFLOW_SRC` is set. `make
  examples-ai-hpc-deterministic` and `make examples-ai-hpc-agentic` create the
  environment and run in a fresh clone.
- ADR mode (`--controller adr`) is documented as unsupported. It needs an ADR
  source checkout (`ADR_SRC`).
- Development uses `uv` and the checked-in `uv.lock`. CI checks formatting with
  the locked Ruff, and builds the documentation strictly from the locked
  environment.
- The documentation site describes 0.3.0: its status, a getting-started page,
  guides for `bind_agent`, for `LangraphIntegration` and for LangGraph on
  AsyncFlow without Flowgentic, a project status page and this changelog. Its
  code blocks include tested files, and the strict build also fails on broken
  links and anchors. The README matches the site's home page.

### Removed

- The `langchain-community` and `langchain-mcp-adapters` dependencies, which
  nothing imported.
- The `flowgentic` console script, whose target `flowgentic:main` did not
  exist.
- The committed results folder `examples/ai-hpc-coupling/agentic_demo_results/`.
- Two unused `create_react_agent` imports from `flowgentic.langGraph`.
- Documentation pages for code that is not on `main`: MCP tools
  (`AGENT_TOOL_AS_MCP`, archived as `archive/feature/mcp`), the framework ×
  backend roadmap, the hierarchical design pattern, which has no example, and
  the chatbot pattern, whose interactive example no test runs.

### Fixed

- Installing the wheel with pip. It could resolve `python-json-logger` 2.x,
  which lacks the `pythonjsonlogger.json` module Flowgentic imports, and
  `pyyaml` was imported but not declared.
- The agentic demos' candidate policy no longer rejects a proposal at a bound
  for rounding alone, and never returns a candidate outside its bounds.
- `create_task_description_handoff_tool`: every handoff failed with
  `AttributeError`, because the tool called a `GraphIntrospector` method removed
  in October 2025. The call is gone, and handoffs work, with Pydantic and
  TypedDict graph states (such as `create_agent`'s).
- The LangGraph examples import again: they used `ConcurrentExecutionBackend`,
  which AsyncFlow 0.3.0 and later do not export, and now use
  `LocalExecutionBackend`.
- Seven `mkdocs build --strict` warnings: three missing annotations and four
  broken links.
- The mutable-graph example's conditional graph always took the standard
  route: its routing flag was missing from the state schema that the graph and
  the router read.
- The supervisor toy example stored agent B's result as `agent_b`, and its
  gather node, which reads `agent_B`, passed "N/A" to the synthesis model.
- CI:
  - `astral-sh/setup-uv` is pinned to an existing release (it publishes no
    `v9` tag).
  - Each test matrix job runs its own Python with the dev extra.
  - The bandit job runs again.

[0.7.0]: https://github.com/stride-research/flowgentic/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/stride-research/flowgentic/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/stride-research/flowgentic/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/stride-research/flowgentic/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/stride-research/flowgentic/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/stride-research/flowgentic/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/stride-research/flowgentic/compare/0.2.0...v0.3.0
