# Fleet

A campaign's agents call models served on the allocation, with one replica
per GPU or server. `flowgentic.fleet.Fleet` is their client: for each call it
chooses the replica and when the call is sent. It records each call, and
waits out a replica whose server went away.

```python
from langchain_openai import ChatOpenAI
from flowgentic.fleet import Fleet, Recovery, TokenAdmission

replicas = [ChatOpenAI(model=model, base_url=url, api_key=key) for url in urls]
fleet = Fleet(
    replicas,
    admission=TokenAdmission(tokens=200_000, requests=96),
    share=1 / processes,                    # this process's share of the caps
    name=model,
)

reply = await fleet.ainvoke(agent_id, messages)          # a graph's own model node
chat = fleet.chat_model().bind_tools(tools)              # a LangChain chat model
```

`flowgentic.fleet` needs the `langgraph` extra, for LangChain. The replicas
are LangChain chat models: anything with `ainvoke(messages)` that returns an
`AIMessage`, and `bind_tools` if the agents call tools.

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
  overtaken forever. Without admission, every call goes at once.
- **It shares the caps among agent processes.** Each process takes `share`
  of every replica's caps. This holds because every process routes the same
  agents to the same replicas.
- **It waits out a server that went away.** With `Recovery(check)`, a call
  that fails because its server is down, restarting or overloaded (a
  connection error, a timeout, a 5xx, or a 401 after a new key) gives up its
  place, waits for the replica to come back, and is sent again.
  `check(i)` returns replica `i`'s model once its server serves again (a new
  one if its address or key changed), or `None` while it is down. A call that
  fails again once its replica is back waits `poll_s` before it is sent
  again. `outage_s` after its first failure, the call fails with its last
  error, even if the replica came back and the call kept failing, or `check`
  did not answer. Other errors fail at once.

A call's live context is estimated before it is sent: the agent's size after
its previous call, as the server counted it, plus the characters added since,
at the tokens per character the server's counts give, plus `reply_reserve`
for the reply. The fleet remembers the sizes of the most recent
`tracked_agents` agents.

## Settings per call

Some calls need settings the others do not, such as a call that may think
within a budget. `ainvoke` and the chat model take them per call:

```python
reply = await fleet.ainvoke(
    agent_id,
    messages,
    extra_body={...},          # any keyword: to the replica, for this call only
    max_tokens=1_024,
    reserve=512,               # tokens added to this call's admission estimate
    meta={"budget": 512},      # carried by this call's record
)
reply = await chat.ainvoke(messages, reserve=512, meta={"budget": 512}, extra_body={...})
```

The keywords reach the replica's `ainvoke` for that call only, through a
`bind_tools` view too, and again if `Recovery` rebuilt the replica's model.
Which settings make a model think is the model's business: the fleet passes
them on. When the server reports a reply's reasoning tokens
(`usage_metadata["output_token_details"]["reasoning"]`), the call's record
carries them, and the agent's size leaves them out: the conversation keeps the
reply, not the thinking.

## What it reports

`fleet.add_listener(listener)` calls `listener(record, reply)` after each
call. The `CallRecord` holds the agent and replica, the wait for admission
and the time at the server, the prompt, cached and completion tokens, the
reasoning tokens if reported, the admission estimate, the gap since the
agent's previous call, the tool whose result came just before, and the
caller's `meta`. `fleet.stats()` adds up the calls, tokens and replies, and
gives each replica's admission and the recovery's outages.

Through the OpenTelemetry API, and only once the application configures an
SDK:

- `flowgentic.fleet.admission.wait` and `flowgentic.fleet.request.duration`
  record each call's wait for admission and its time at the replica;
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
the host.
