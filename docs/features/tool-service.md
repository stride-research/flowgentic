# Tool service

A campaign runs its agents in many processes, and their tools may be MCP
tools. An MCP client in every agent process would hold its own connections,
and the MCP SDK and its web stack would cost each process tens of megabytes.
The tool service is one process that calls MCP tools for all of them: agent
processes send their calls on a queue, and the service answers each process on
a queue of its own.

```python
from flowgentic.tools.mcp import SupervisedMcpPool
from flowgentic.tools.service import ToolServiceClient, serve

# In the service process
pool = SupervisedMcpPool(urls, clients=8, per_client=16)
stats = await serve(requests, replies, pool)       # until None arrives on requests

# In agent process i
client = ToolServiceClient(requests, replies[i], index=i)
client.start()                                     # on the process's event loop
result = await client.call(uid, "lookup", {"key": "x"})
```

The queues are the caller's: anything with `put(bytes)`, `get(timeout=...)`
and `get(block=False)` that raises `queue.Empty`, such as Dragon's queues,
`multiprocessing.Queue` or `queue.Queue`. Flowgentic imports nothing from
Dragon. `flowgentic.tools.service` needs no extra, so agent processes import no
MCP client. `flowgentic.tools.mcp` needs the `mcp` extra.

## What the service does

- **It batches.** Calls an agent process makes in one pass of its event loop
  go out together, and answers go back together, up to 512 a message.
- **It answers a repeated call once.** A call carries a uid. A second call
  with the same uid joins the first while it runs, and gets the kept answer at
  once after it has ended, so an agent that resumes and asks again does not run
  the tool twice. The service keeps every answer until it stops.
- **It reports.** `serve` returns how many requests it received, how many
  distinct calls it made, the repeats, and the outcomes per tool.
  `listener(uid, tool, received, called, answered, ok)` receives each call's
  times, for whoever keeps them.

Requests and answers are pickled. That is safe only because both ends are
processes of the same job: the queues must not be reachable from outside it.

## The supervised MCP pool

`SupervisedMcpPool` is the service's MCP client: `clients` connections to one
or more Streamable HTTP servers, each carrying up to `per_client` calls at
once, all pulling from one queue.

In the MCP Python SDK 2.3, a network error on one request cancels every call
in flight on that client, and the task that opened the client too
([python-sdk#3522](https://github.com/modelcontextprotocol/python-sdk/issues/3522)).
So the pool opens each client in a supervisor task of its own, and guarantees:

- **A failed connection fails only its own calls.** The calls in flight on it
  fail with `McpConnectionLost`; queued calls wait for another client. The
  caller's task is never cancelled.
- **It reconnects with backoff.** Exponential, with jitter, reset once a
  connection has served a call.
- **It spreads clients over servers.** Client `i` connects to server
  `i mod servers`, and the clients share one queue, so a faster server takes
  more calls and a server that is down holds none.
- **It fails fast when nothing is connected.** After `unavailable_after`
  seconds with no client connected, calls fail at once with `McpUnavailable`
  until a client reconnects.
- **A call's timeout covers its wait in the queue too.** A caller that stops
  waiting also stops its call, which frees the slot.
- **It resends only what is safe to repeat.** A call that lost its connection
  may or may not have run on the server. It is sent again only with
  `idempotent=True`, at most `resends` times. Pass `idempotent=True` to
  `ToolServiceClient.call` only for tools whose execution can safely repeat.
  A uid deduplicates requests at the service; a server can still have executed
  a call whose reply was lost.

`close()` stops intake, lets calls finish for up to `drain` seconds, fails the
rest with `McpPoolClosed`, and never raises. `pool.stats()` returns the pool's
state, its counters and its last errors.
