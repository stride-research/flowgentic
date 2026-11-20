# Changelog

All notable changes to Flowgentic are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Tags `v0.1.0` (`3e43c13c`, 23 Sep 2025) and `0.2.0` (`72e972d4`, 2 Oct 2025)
predate this changelog, so their changes are not listed here.

## [Unreleased]

### Added

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
  names its own, on a session as on an engine.
- On an engine, a task belongs to the AsyncFlow workflow scope or block its
  agent runs in: AsyncFlow labels it with the scope's id, and cancelling the
  block cancels the task, which ends with `TaskCancelled`. A tool's `backend=None` names
  none.
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

[Unreleased]: https://github.com/stride-research/flowgentic/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/stride-research/flowgentic/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/stride-research/flowgentic/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/stride-research/flowgentic/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/stride-research/flowgentic/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/stride-research/flowgentic/compare/0.2.0...v0.3.0
