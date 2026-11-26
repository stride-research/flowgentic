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
    process=(index, processes),             # this process's exact part of the caps
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
POST to `{base_url}/chat/completions` per call. The key goes as a bearer
token: by default `EMPTY`, which vLLM and SGLang accept when they set none,
and `OPENAI_API_KEY` from the environment for OpenAI's own API
(`api.openai.com`) only, so that a server on an allocation, often reached over
plain HTTP, is never sent an OpenAI key it was not given; `OpenAITransport`
does the same, where the `openai` SDK alone sends that key to any server. A
call times out after `timeout`, 600 s by default (`None` for none), and a
connection after 5 s at most, as with the SDK. `base_url` must be an http or
https URL: anything else is a `ValueError` when the transport is made. Keywords given when it is made, such as
`temperature=0`, go with every call, and a call's own override them;
`extra_body`'s keys go into the request's body and `extra_headers`' into its
headers, as the `openai` SDK sends them. It retries nothing: `Recovery` waits
out a server that went away.

`OpenAITransport(model, base_url=..., api_key=...)` calls the same servers
through the `openai` SDK's `AsyncOpenAI`, which retries a failed call itself
unless given `max_retries=0`. It costs about four times the CPU per call of
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
| `ServerUnavailable` | no answer (a refused or dropped connection), a timeout, a 5xx, or a busy server's 408, 409 or 429, which the `openai` SDK retries too | yes |
| `AuthenticationFailed` | 401, as from a server restarted with a new key | yes |
| `RequestRejected` | any other 4xx, such as a context too long | no |
| `ModelServerError` itself | a completion with no choices and no error code, a reply that is not JSON or that httpx cannot decode, too many redirects, a header that cannot be sent (a line break, non-ASCII text), a call after `aclose()` | no |

An error object that a server sends with status 200 and no choices, as some
vLLM releases and OpenRouter do, is read by its `code` as an HTTP status: an
OpenRouter 429 or 502 is `ServerUnavailable`, vLLM's 400 `RequestRejected`;
`status_code` carries the code. A 401 from a server that is not OpenAI's,
with no `api_key` given, says that `OPENAI_API_KEY` is sent to
api.openai.com only. Text that UTF-8 cannot carry, such as a lone surrogate
in a file name Linux could not decode, goes as its escape, as `ensure_ascii`
writes it, rather than failing every later call.

`Recovery` waits out a transient one, and the `openai` SDK's errors of the same
kinds. It sends the call again once its check finds the server, after
`poll_s` at least; it does not read a `Retry-After` header.

A client's CPU cost per call grows with the calls its connection pool holds
in flight. So both transports send each call to their least busy client, and
open another client, with its own pool, whenever each one holds
`calls_per_pool` calls: 8 for `HTTPTransport`, 64 for `OpenAITransport`. A
replica with 512 calls in flight then has 64 clients of `HTTPTransport`, one
with a few calls has one. `pools=N` fixes the number of clients instead.
All the pools of all the `HTTPTransport`s in a process share one SSL
context, built from `SSL_CERT_FILE` or `SSL_CERT_DIR` as they are when the
transport is made. With 400 replicas, a process sets up in 0.1 s and a few
MB, where a context per transport took 1.5 s and 300 MB. A pool connects to
its server on its first call. `aclose()`, on both transports, refuses new
calls at once and returns, and the clients the
transport made close as soon as their calls in flight end; the fleet closes a
transport that `Recovery` replaces. A `client` given (an `httpx.AsyncClient`;
for `OpenAITransport`, an `AsyncOpenAI`) is the only one, and the transport
never closes it. It keeps its own headers unless the transport is given an
`api_key`, and its own timeout unless the transport is given a `timeout`, or
the client has httpx's default of 5 s, which a model call outlasts: it then
gets the transport's 600 s, as the `openai` SDK does with a bare
`http_client`. `transport.timeout` is the timeout its calls get.

Measured against a local stub server that answers after a vLLM server's time
at each load (1.3 s at 64 calls in flight, 8.9 s at 512), on an AMD EPYC 7502P
pinned to 8 cores, the client's CPU per call, median of three runs:

| Client | 64 in flight | 512 in flight |
| --- | --- | --- |
| plain httpx, one pool | 1.50 ms | 29.0 ms, and behind the server |
| `HTTPTransport`, 64 calls per pool | 1.47 ms | 1.41 ms |
| `HTTPTransport`, 32 calls per pool | 1.08 ms | 1.00 ms |
| `HTTPTransport`, 16 calls per pool | 0.89 ms | 0.85 ms |
| `HTTPTransport`, 8 calls per pool, the default | 0.81 ms | 0.79 ms |
| `HTTPTransport`, 4 calls per pool | 0.88 ms | 0.77 ms |
| `OpenAITransport`, as made by default | 3.30 ms | 3.07 ms |

The 4-per-pool row ran on another day, with 8 per pool beside it (0.82 and
0.79 ms). `benchmarks/client_cost.py` measures a client this way.

A LangChain chat model given as a replica, or returned by `Recovery`'s check,
runs through LangChain's adapter, `flowgentic.langchain.ChatModelTransport`.
It takes OpenAI-format messages and JSON schemas, and also LangChain messages
and tools as they are, so a LangGraph graph's calls reach the model
unchanged, and the reply's `raw` is the model's own `AIMessage`.

## What the fleet does

- **It keeps each agent on one replica.** `AgentAffinity`, the default, sends
  an agent's calls to one replica, chosen by a hash of the agent's id that is
  the same in every process; with `process=`, among the replicas its process
  sends to. The agent's prompt grows by a few messages per call, so the
  replica's prefix cache holds most of it. `RoundRobin` and `LeastLoaded`
  are there to compare with.
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
- **It splits the caps among agent processes.** Each process holds a part
  of the replicas' caps and admits its own calls under it (see
  [Many agent processes](#many-agent-processes)). A process cannot see the
  others' calls, so a call whose context is larger than the process's part
  of a replica's token cap is admitted alone, once nothing else of the
  process is in flight at that replica; one such call takes the replica past
  its cap while the other processes use their parts. Each gate counts such
  calls (`oversize` in `fleet.stats()["gates"]`), and the fleet logs the
  first as a warning, once for all its replicas. Choose the caps so that the
  largest context fits in the smallest part (see
  [The smallest part](#the-smallest-part)).
- **It waits out a server that went away.** With `Recovery(check)`, a call
  that fails because its server is down, restarting, overloaded or busy (a
  connection error, a timeout, a 5xx, a busy server's 408, 409 or 429, or a
  401 after a new key: a transient `ModelServerError`, or the `openai` SDK's
  error of that kind) gives up its place, waits for the replica to come back,
  and is sent again. Other errors fail at once.

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
endpoints = {i: (url, key) for i, (url, key) in enumerate(servers)}

async def check(i: int):
    url, key = read_endpoint(i)            # the application's: where replica i serves now
    if not await healthy(url):             # for example, GET /health with its own timeout
        return None
    if endpoints[i] == (url, key):         # the same server: keep its transport and pools
        return replicas[i]
    endpoints[i] = (url, key)              # moved or rekeyed: a new transport;
    replicas[i] = HTTPTransport(model, base_url=url, api_key=key)
    return replicas[i]                     # the fleet closes the one it replaces

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

## Many agent processes

The caps of `TokenAdmission` and `RequestAdmission` are a replica's, across
all the agent processes that send to it. Each process tells the fleet which
one it is, and holds its part of the caps:

```python
fleet = Fleet(replicas, admission=admission, process=(index, processes))
```

- **The parts are exact.** The processes split each replica's caps so that
  their parts add up to the cap and none is above it. Each process holds an
  equal part of the fleet's requests, to one request. Over 4 replicas with a
  cap of 256 requests, 100 processes each hold 2 or 3 requests at every
  replica; 300 processes each hold one request at 3 or 4 replicas, and none
  at the others.
- **A process sends only where it holds a part.** It routes each agent to
  one of those replicas, by the agent's hash, in proportion to its parts of
  them. A replica it holds none of gets no call from it and no metrics, and
  keeps a load of 0 in `fleet.load()`; its gate in `fleet.stats()["gates"]`
  has the keys of the others, with caps and counts of 0. In
  `fleet.stats()`, `replicas_used` lists the replicas the process sends to,
  `process` is `[index, processes]`, and `share` is `None`.
- **Spread or packed.** With `layout="spread"`, the default, a process takes
  its part from every replica, or from as many as its part allows. With
  `layout="pack"`, it takes it from as few replicas as hold it, in their
  order: it holds the whole caps of the replicas inside its run of them, and
  part of the caps of the one or two where its run starts and ends, unless
  the run starts and ends at a replica's edge. When the processes divide the
  replicas evenly, every part is whole. A process connects only to the
  servers it sends to, since a transport connects on its first call. With
  400 replicas and 100 processes, each holds 4 whole replicas; with 4
  replicas and 3 processes, the first holds replica 0 and a third of
  replica 1.
- **Packed, a process's replicas can be on its node.** The fleet does not
  know where the servers run. List the replicas in the order of their
  nodes, and number the processes in the order of theirs. When every node
  has the same number of replicas and the same number of agent processes,
  each process's replicas are on its own node. With 8 replicas and 2
  processes per node, process `2k` holds the first four of node `k`'s
  replicas, and process `2k + 1` the other four.

```python
fleet = Fleet(replicas, admission=admission, process=(index, processes), layout="pack")
```

### Every process needs the same inputs

The processes agree on their parts without talking to each other: each
works out its own from the same numbers. So every process must be given the
same replicas, in the same order, the same admission, and the same
`processes` and `layout`, and each index from 0 to `processes - 1` must be
used once. Nothing checks this: a process given other inputs holds other
parts, and the replicas may get more calls than their caps, or fewer. For
the same reason, the parts are fixed while the processes run:

- Adding or removing a replica changes every process's parts, so every
  process must be restarted with the new list.
- All the replicas share one admission. Replicas with different caps, such
  as servers on GPUs of two kinds, cannot each have caps of their own: give
  the fleet the caps of the smallest.

### The smallest part

The fleet cuts each replica's caps into as many units as the admission's
request cap, and gives each process a run of units, so a process always
holds whole requests. Its part of a replica's token cap follows its
requests: the token cap ÷ the request cap for each request, rounded down or
up by one token. At each replica it sends to, a process holds at least as
many requests as `share=1 / processes` gave it: the request cap ÷ the
processes, rounded down, and at least 1.

A context larger than a process's part of a replica's token cap is
admitted alone, above the part (an `oversize` call), so the largest context
should fit in the smallest part: the token cap ÷ the request cap × the
fewest requests a process holds. A request cap of at most the token cap ÷
the largest context makes every part fit.

The smallest part can be smaller than the token cap ÷ the processes, which
`share` gave every process. Over 4 replicas with caps of 235,000 tokens and
256 requests, 100 processes each hold 2 or 3 requests at a replica, so
1,835 tokens at the least, where `share=1 / 100` gave each process 2,350.
With 60 processes over one replica of 96 requests, 24 processes hold one
request and 2,447 tokens, where `share=1 / 60` gave each 3,916.

`Fleet` raises `ValueError`, in every process alike, when the fleet has
fewer requests in all than there are processes, since a process would hold
none, and when the token cap is below the request cap, since some requests
would come with no token.

### `share`, as in 0.6.0

`share` still works: each process holds `share` of every replica's caps,
rounded down, and at least 1. With 100 processes and a cap of 256,
`share=1 / 100` gives each 2 requests, so the replicas run at 200 of 256;
with 300 processes, 1 each, so a replica may get 300 requests against its
256. Give `share` or `process`, not both.

Where the processes divide the request cap, `process=` with the default
layout gives each process the same requests at every replica as
`share=1 / processes`, and sends each agent to the same replica. Its token
parts are the same where the processes also divide the token cap, and
otherwise one token larger at most.

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
names; without LangChain, `flowgentic.fleet.FleetChatModel` raises an
`AttributeError` that names the `langgraph` extra, so that `hasattr` and
`import *` work, and `from flowgentic.fleet import FleetChatModel` a plain
`ImportError`: Python turns a module's `AttributeError` into that, without
its message, for a `from` import. `from flowgentic.langchain import
FleetChatModel` names the extra), for prebuilt
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
`share` of the replica's caps. `share` is a float, the fraction of the caps.
With `process=`, it is a `flowgentic.fleet.Share`, which the fleet makes. As
a float, it is the fraction of the replica's caps this process holds, and it
prints, compares and hashes as that float: two processes' shares of a
replica are equal when their fractions are, whichever units each holds.
`share.part(cap)` gives the process its exact part of any cap, and the
parts of the processes add up to the cap, as `TokenAdmission` and
`RequestAdmission` use it.

The fleet cuts each replica into as many units as the admission's
`requests` attribute, its request cap, if it has one (an integer of at
least 1), so that each process holds whole requests. An admission without
one is cut into one unit per process, and a cap smaller than the processes
can then leave a process a part of 0 at a replica it sends to: give a
policy with a request cap that cap as `requests`. The fleet calls `gate`
only for the replicas the process holds a part of. A gate follows the
`Gate` protocol:

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

- each call is one GenAI `chat` span, from the moment the fleet routes it to
  its reply, however many tries it took: its wait for admission, every try,
  and any outage it waited out. It carries the request's and the response's
  model, the server's address and port (those of the replica that answered,
  if `Recovery` replaced it, as far as it names them: a span cannot drop an
  attribute), the finish reason, and the usage the server
  reported (`gen_ai.usage.input_tokens`, `output_tokens`,
  `cache_read.input_tokens`, `reasoning.output_tokens`; a breakdown of zero,
  or one not reported, is left out). The agent's id is its
  `gen_ai.conversation.id`. Flowgentic adds the call's own attributes, below.
  A call that failed, or was cancelled, has status ERROR and `error.type`.
  With LangChain replicas and OpenTelemetry's LangChain instrumentation on,
  the instrumentation's span is the call's, and the fleet records none, so no
  usage is counted twice;
- `flowgentic.fleet.call.duration` records each call's time, as its span
  does, with `error.type` for a call that failed or was cancelled; its count
  is the fleet's calls;
- `flowgentic.fleet.admission.wait` and `flowgentic.fleet.request.duration`
  record each try's wait for admission and its time at the replica, so a
  retried call records one of each per try; a try that failed, retried or
  not, carries its error's class in `error.type`, and one cancelled at the
  replica, which the server saw, `CancelledError`;
- `flowgentic.fleet.retries` counts the requests sent again once their
  replica came back, with the `error.type` of the failure before each, when
  each is admitted again: a call cancelled before that sent none;
- `flowgentic.fleet.outage.duration` records each outage that `Recovery`
  watched, from the call that found the replica down to its return or to
  `Recovery` giving up, with `flowgentic.fleet.outage.outcome`, `recovered`
  or `gave_up`. Calls that fail while one watch runs share its outage;
- `flowgentic.fleet.tokens.active`, `flowgentic.fleet.requests.active` and
  `flowgentic.fleet.requests.waiting` give each replica's tokens and requests
  in flight, and its requests waiting, read when the SDK collects;
- `flowgentic.fleet.tokens` counts the prompt (`gen_ai.token.type` `input`)
  and completion (`output`) tokens the servers counted at each replica, read
  when the SDK collects, so it costs nothing per call and holds however the
  spans are sampled. Fleets of one name in a process add up, and a fleet
  that is gone keeps its count.

Each carries `flowgentic.fleet.name`, the fleet's `name`, and
`flowgentic.fleet.replica`, the replica's index. The `chat` span adds what
the call's `CallRecord` holds about its times and its place in the agent's
conversation:

| Attribute | Value |
| --- | --- |
| `flowgentic.fleet.admission_wait` | Seconds the call waited for admission, over all its tries, a wait that a cancellation cut short included |
| `flowgentic.fleet.resend_count` | Requests sent again, if any |
| `flowgentic.fleet.recovery_wait` | Seconds in the tries that failed and in the waits for the replica, if a try failed with an error `Recovery` waits out |
| `flowgentic.fleet.gap` | Seconds since the agent's previous call through the fleet ended; absent on its first |
| `flowgentic.fleet.after_tool` | The tool whose result is the conversation's last message, if one is |

The cached tokens, the gap and the tool before tell, call by call, how much
of a prompt the server found in its prefix cache, and after what.

Counts read from these metrics stay exact through outages and
cancellations. The calls are `flowgentic.fleet.call.duration`'s count. The
requests the servers saw are `flowgentic.fleet.request.duration`'s: the
calls that reached a replica, plus the requests sent again. A call cancelled
while it waited for admission is a call that sent no request. [Telemetry](telemetry.md#read-an-export-back) reads an export per
process back and checks it.

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
