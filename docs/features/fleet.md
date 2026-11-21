# Fleet

A campaign's agents call models served on the allocation, with one replica
per GPU or server. `flowgentic.fleet.Fleet` is their client: for each call it
chooses the replica and when the call is sent. It records each call, and
waits out a replica whose server went away.

```python
from flowgentic.fleet import Fleet, HTTPTransport, Recovery, TokenAdmission

replicas = [HTTPTransport(model, base_url=url, api_key=key) for url in urls]
fleet = Fleet(
    replicas,
    admission=TokenAdmission(tokens=200_000, requests=96),
    share=1 / processes,                    # this process's share of the caps
    name=model,
)

messages = [{"role": "user", "content": "Find the best temperature for cand-000042."}]
reply = await fleet.ainvoke(agent_id, messages, tools=schemas)   # a Reply
chat = fleet.chat_model().bind_tools(tools)                       # a LangChain chat model
```

`flowgentic.fleet` needs no extra, and neither does its default transport,
`HTTPTransport`: httpx is one of Flowgentic's own dependencies.
`OpenAITransport` needs the `openai` extra; LangChain chat models as
replicas, and `chat_model()`, need the `langgraph` extra.

## Replicas are transports

A replica is a `Transport`: an object with one method,

```python
async def complete(self, messages, tools=None, **kwargs) -> Reply: ...
```

that sends a conversation, as OpenAI Chat Completions messages (`role`,
`content`, `tool_calls` with JSON-string arguments, `tool_call_id`), with the
tools' JSON schemas and the call's own keywords (`max_tokens`, `extra_body`,
...), to one model server, and returns its `Reply`:

| `Reply` | |
| --- | --- |
| `content` | the answer's text |
| `tool_calls` | `ToolCall(id, name, args)`, the arguments parsed |
| `invalid_tool_calls` | `InvalidToolCall(id, name, arguments, error)`: calls whose arguments are not a JSON object, for the model to read why |
| `usage` | `Usage(prompt_tokens, completion_tokens, cached_tokens, reasoning_tokens)`; the last two are `None` when the server does not say |
| `reasoning` | the thinking the server split out of the answer |
| `finish_reason`, `id`, `model` | the server's |
| `raw` | the transport's own response |

`reply.message()` is the reply as the conversation keeps it: an assistant
message with the content and the tool calls, without the reasoning.

`HTTPTransport(model, base_url=..., api_key=...)`, the default, calls an
OpenAI-compatible server, vLLM, SGLang, OpenRouter or OpenAI, with one httpx
POST to `{base_url}/chat/completions` per call. The key, by default
`OPENAI_API_KEY` or else `EMPTY`, goes as a bearer token, and a call times out
after `timeout`, 600 s by default. Keywords given when it is made, such as
`temperature=0`, go with every call, and a call's own override them;
`extra_body`'s keys go into the request's body and `extra_headers`' into its
headers, as the `openai` SDK sends them. It retries nothing: `Recovery` waits
out a server that went away.

`OpenAITransport(model, base_url=..., api_key=...)` calls the same servers
through the `openai` SDK's `AsyncOpenAI`, which retries a failed call itself
unless given `max_retries=0`. It costs about twice the CPU per call of
`HTTPTransport` (below), and needs the `openai` extra.

Both read the reply with `parse_completion`: the reasoning text from the
message's `reasoning`, which vLLM uses since it [renamed the
field](https://docs.vllm.ai/en/latest/features/reasoning_outputs/), and from
`reasoning_content`, which older vLLM and SGLang use; the cached prompt tokens
from `usage.prompt_tokens_details`, and the reasoning tokens from
`usage.completion_tokens_details`, when the server sends them (vLLM sends the
first with `--enable-prompt-tokens-details`). `parse_completion` reads such a
server's completion, as the SDK's object or as its JSON, for a transport of
your own.

A failed call through `HTTPTransport` raises a `ModelServerError`, with the
server's message, its `status_code` and its `body`:

| Error | When | `transient` |
| --- | --- | --- |
| `ServerUnavailable` | no answer (a refused or dropped connection), a timeout, or a 5xx | yes |
| `AuthenticationFailed` | 401, as from a server restarted with a new key | yes |
| `RequestRejected` | any other 4xx, such as a context too long | no |

`Recovery` waits out a transient one, and the `openai` SDK's errors of the same
kinds, which it knows by name.

A client's CPU cost per call grows with the calls its connection pool holds
in flight. Against a local stub server, one pool cost 3.5 ms of CPU per call
at 64 calls in flight, and 24 to 31 ms at 512 (plain httpx too); the same 512
calls spread over eight clients of 64 cost 3.5 ms. So `OpenAITransport` sends
each call to its least busy client, and opens another client, with its own
pool, whenever each one holds 64 calls (`calls_per_pool=64`): a replica with
512 calls in flight has eight, one with a few calls has one. `pools=N` fixes
the number of clients instead, and a `client` given is the only one.

A LangChain chat model given as a replica, or returned by `Recovery`'s check,
runs through LangChain's adapter, `flowgentic.langchain.ChatModelTransport`.
It takes OpenAI-format messages and JSON schemas, and also LangChain messages
and tools as they are, so a LangGraph graph's calls reach the model
unchanged, and the reply's `raw` is the model's own `AIMessage`.

## What the fleet does

- **It keeps each agent on one replica.** `AgentAffinity`, the default, sends
  an agent's calls to one replica, chosen by a hash of the agent's id that is
  the same in every process. The agent's prompt grows by a few messages per
  call, so the replica's prefix cache holds most of it. `RoundRobin` and
  `LeastLoaded` are there to compare with.
- **It admits calls to a replica under caps.** Once a model server's KV cache
  cannot hold the contexts of the agents it serves, its prefix cache evicts
  the contexts about to be reused, and every call recomputes its history.
  `TokenAdmission(tokens, requests)` keeps each replica under a cap on the
  tokens of live context in flight and one on the requests in flight, and
  lets other calls wait. `RequestAdmission(requests)` caps requests only.
  Calls are admitted first come, first served, so a long context is never
  overtaken forever. Without admission, every call goes at once. Other
  policies plug in through the same argument (see
  [Other admission policies](#other-admission-policies)).
- **It shares the caps among agent processes.** Each process takes `share`
  of every replica's caps. This holds because every process routes the same
  agents to the same replicas. A process cannot see the others' calls, so a
  call whose context is larger than the process's share of the token cap is
  admitted alone, once nothing else of the process is in flight at that
  replica; one such call takes the replica past its cap while the other
  processes use their shares. Each gate counts such calls (`oversize` in
  `fleet.stats()["gates"]`), and the fleet logs the first as a warning,
  once for all its replicas. Choose the cap and `share` so that the largest
  context fits in one share.
- **It waits out a server that went away.** With `Recovery(check)`, a call
  that fails because its server is down, restarting or overloaded (a
  connection error, a timeout, a 5xx, or a 401 after a new key: a transient
  `ModelServerError`, or the `openai` SDK's error of that kind) gives up its
  place, waits for the replica to come back, and is sent again. Other errors
  fail at once.

  `check` is an `async def`: `check(i)` returns replica `i`'s transport once
  its server serves again (a new one if its address or key changed), or
  `None` while it is down. The first failing call on a replica starts one watch,
  which calls `check(i)` every `poll_s` seconds until the replica serves
  again or `outage_s` has passed; later failing calls wait on the same watch.
  A call whose watch gives up, because it started earlier for another call,
  starts a new one if at least `poll_s` of the call's own `outage_s` is
  left; with less, it fails then.
  A check that raises, or does not answer within `check_timeout_s` (30 s by
  default), counts as the replica still being down: the watch logs it, counts
  it in `fleet.stats()["recovery"]["check_failures"]`, and keeps polling. The watch runs in a
  context of its own, not in the context of the agent whose call failed
  first.

  A call that fails again once its replica is back waits `poll_s` before it
  is sent again. `outage_s` after its first failure, the call fails with its
  last error, even if the replica came back and the call kept failing, or
  `check` did not answer.

```python
async def check(i: int):
    url, key = read_endpoint(i)            # the application's: where replica i serves now
    if not await healthy(url):             # for example, GET /health with its own timeout
        return None
    return HTTPTransport(model, base_url=url, api_key=key)

fleet = Fleet(replicas, recovery=Recovery(check, outage_s=1_800, poll_s=5))
```

A call's live context is estimated before it is sent: the agent's size after
its previous call, as the server counted it, plus the characters added since,
at the tokens per character the server's counts give, plus `reply_reserve`
for the reply. An agent's first call is estimated from the server's counts
for earlier first calls, as a fixed part, such as tool schemas and a template
that the messages do not show, plus tokens per character. The tokens per
character are never fewer than those of the characters later calls add, so a
first call is never estimated below its text alone, also while the first
prompts' lengths barely vary, or when agents with and without tool schemas
mix. `fleet.stats()["tokens_per_char"]` gives what was
learned: `first_call` and `first_call_base` (the fixed part, in tokens), and
`added`. The fleet remembers the sizes of the most recent `tracked_agents`
agents.

## Settings per call

Some calls need settings the others do not, such as a call that may think
within a budget. `ainvoke` and the chat model take them per call:

```python
reply = await fleet.ainvoke(
    agent_id,
    messages,
    tools=schemas,             # the tools' JSON schemas
    extra_body={...},          # any keyword: to the replica, for this call only
    max_tokens=1_024,
    reserve=512,               # tokens added to this call's admission estimate
    meta={"budget": 512},      # carried by this call's record
)
reply = await chat.ainvoke(messages, reserve=512, meta={"budget": 512}, extra_body={...})
```

The keywords reach the transport's `complete` for that call only, and again
if `Recovery` rebuilt the replica.
The chat model takes them wherever it can tell the agent (see below), and
also bound in advance, as in `chat.bind(reserve=512)`.
Which settings make a model think is the model's business: the fleet passes
them on. When the server reports a reply's reasoning tokens
(`Reply.usage.reasoning_tokens`), the call's record
carries them, and the agent's size leaves them out: the conversation keeps the
reply, not the thinking.

## The chat model

`fleet.chat_model()` is the fleet as a LangChain chat model
(`flowgentic.langchain.FleetChatModel`, which `flowgentic.fleet` still
names), for prebuilt
agents such as LangChain's `create_agent` and LangGraph's
`create_react_agent`, and for graphs that expect one. Its `bind_tools` and
`with_structured_output` work as a chat model's do.

It routes each call by the run config's `thread_id`: the one in the config
passed to `ainvoke`, or else the one of the LangGraph run it is called in.
So a node can call it for a sub-agent with the sub-agent's own thread:

```python
await chat.ainvoke(messages, config={"configurable": {"thread_id": sub_agent}})
```

Without a `thread_id`, it uses the agent the [agent host](agent-host.md) is
running, and without either it raises `ValueError`.

The chat model opens no LangChain run of its own. Over LangChain replicas it
hands the caller's messages, tools and config to the replica as they are, so
the replica's run is the call's only one: callbacks, LangSmith and
OpenTelemetry's GenAI instrumentation see one model call with the replica's
usage, LangGraph's `stream_mode="messages"` streams the replica's tokens, and
the reply is the replica's own `AIMessage`. Over other transports it converts
the messages and tools to OpenAI's format, and the `Reply` to an `AIMessage`
(`flowgentic.langchain.to_ai_message`); of `bind_tools`' keywords it sends
`tool_choice` and `parallel_tool_calls` as `ChatOpenAI` does (`"any"` is
`"required"`, a tool's name a choice of that tool), applies `strict` to the
tools' schemas, and refuses any other. It is asynchronous only: call
`ainvoke`. Its cost per call, a LangChain call more than `fleet.ainvoke`, is
why a graph's own model node may call the fleet directly, and turn the reply
into a message with `to_ai_message`, as the
[agents-at-scale](../guides/agents-at-scale.md#with-langgraph) example's
LangGraph version does.

## Other admission policies

`admission=` takes any `Admission`: an object with `counts_tokens` and a
`gate(share)` method that makes one gate per replica, for this process's
`share` of the replica's caps. A gate follows the `Gate` protocol:

```python
class Gate(Protocol):
    async def acquire(self, n: int, agent_id: str) -> None: ...
    def release(self, n: int, agent_id: str) -> None: ...
    def agent_done(self, agent_id: str) -> None: ...
    def load(self) -> ReplicaLoad: ...
    def stats(self) -> dict: ...
```

Before it sends an agent's call to the replica, the fleet awaits
`acquire(n, agent_id)`, where `n` is the call's estimated tokens of live
context, or 0 if `counts_tokens` is false. Once the call is over, whether it
succeeded, failed or was cancelled, the fleet calls `release` with the same
arguments. A call that waits out a recovery releases first and acquires again
before it is sent again. If `acquire` is cancelled, it must leave the gate as
it found it. `load()` gives the replica's tokens and requests in flight and
its requests waiting, for `fleet.load()`, the routing and the metrics, and
`stats()` goes into `fleet.stats()["gates"]`. `TokenAdmission` and
`RequestAdmission` make `AdmissionGate`s, which admit first come, first
served, whatever the agent. A gate that is not a `Gate` is refused when the
fleet is built.

A policy that keeps something per agent, such as the agents whose contexts
it holds in a replica's cache, needs to hear when an agent ends.
`fleet.agent_done(agent_id)` tells every gate (`agent_done`) and drops the
agent's size from the fleet's estimates. Call it once the agent makes no more
calls; a call of that agent may still be in flight.

## What it reports

`fleet.add_listener(listener)` calls `listener(record, reply)` after each
call that returns, with the call's `Reply`. The `CallRecord` holds the agent and replica, the
prompt, cached and completion tokens, the reasoning tokens if reported, the
admission estimate, the gap since the agent's previous call, the tool whose
result came just before, and the caller's `meta`. Its times add up to the
call's: `wait_s`, the wait for admission over all the call's tries;
`service_s`, the time the try that succeeded spent at the replica; and
`recovery_s`, when `Recovery` waited out an outage, the tries that failed
and the wait for the replica to come back. `fleet.stats()` adds up the
calls, tokens, replies and errors, and gives each replica's admission and
the recovery's outages.

Through the OpenTelemetry API, and only once the application configures an
SDK:

- each try is a GenAI `chat` span, with the request's and the response's
  model, the server's address and port, the finish reason, and the usage the
  server reported (`gen_ai.usage.input_tokens`, `output_tokens`,
  `cache_read.input_tokens`, `reasoning.output_tokens`; a breakdown of zero, or
  one not reported, is left out). A failed try's span has status ERROR and
  `error.type`. With LangChain replicas and OpenTelemetry's LangChain
  instrumentation on, the instrumentation's span is the call's, and the fleet
  records none, so no usage is counted twice;
- `flowgentic.fleet.admission.wait` and `flowgentic.fleet.request.duration`
  record each try's wait for admission and its time at the replica, so a
  retried call records one of each per try; a try that failed, retried or
  not, carries its error's class in `error.type`;
- `flowgentic.fleet.tokens.active`, `flowgentic.fleet.requests.active` and
  `flowgentic.fleet.requests.waiting` give each replica's tokens and requests
  in flight, and its requests waiting, read when the SDK collects.

Each carries `flowgentic.fleet.name`, the fleet's `name`, and
`flowgentic.fleet.replica`, the replica's index.

## In an agent host

In an [agent host](agent-host.md), the fleet counts each agent as
`MODEL_QUEUED` while its call waits for admission and as `MODEL_REQUEST` while
the call is at the server. The chat model reads the agent's id from the run
config's `thread_id`, which `GraphRunner` sets to the agent's id, or else from
the host. The host's `on_done` is the place to tell the fleet that an agent
ended:

```python
host = AgentHost(runner, on_done=lambda result, output: fleet.agent_done(result.id))
```
