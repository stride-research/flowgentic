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
result = await client.call(uid, "lookup", {"key": "x"}, idempotent=True)
```

Pass `idempotent=True` for a tool that can safely run twice, such as a lookup.
Then a call that lost its connection is sent again, and a failure that left its
outcome unknown can be retried. Leave it out for a tool with side effects, such
as one that writes or submits: the service then never sends it twice.

A call's arguments travel as JSON values, as MCP takes them. The client
converts pydantic models, enums and other values that have a JSON form before
it sends them, so the service process never needs the agent's own classes.
Arguments with no JSON form fail their own call at once, with
`ToolServiceError`.

## When the service is gone

If the service process dies, for example killed for using too much memory,
its answers never come. So every call has a deadline: `ToolServiceClient`'s
`timeout`, 150 s by default, the service's default `call_timeout` (120 s) plus
a margin for the queues. A call with no answer by then fails with
`ToolServiceError`, and the agent reads that its tool failed. Keep `timeout`
above the `call_timeout` the service runs with, or slow calls will fail at the
client while the service still runs them. Asking again with the same uid sends
the request again, and a service that is still running answers from its kept
answer.

To fail sooner, pass `alive`, a function that tells whether the service is
still running. The client asks it in the agent process, from its reader
thread, about once a second while no answer arrives. Once it returns false,
the client fails every waiting call and stops. For a service on the same node,
test its pid, which the launcher can pass to each agent process:

```python
import os

def service_running() -> bool:
    try:
        os.kill(service_pid, 0)   # signal 0 only checks that the process exists
    except ProcessLookupError:
        return False
    return True

client = ToolServiceClient(requests, replies[i], index=i, alive=service_running)
```

A killed process keeps its pid until its parent reaps it, as
`multiprocessing` does when the launcher joins it or calls `is_alive`; until
then the check says it runs, and the deadline still applies. A
`multiprocessing.Process`'s `is_alive` works only in the process that started
the service, so it is no check for an agent process. A check that raises counts
as the service running: the first failure is logged as a warning, with its
traceback, and later ones at debug level.

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
  the tool twice. The service keeps each final answer until it stops: the
  tool's result or its own error, or a failure after which a call that is not
  idempotent may have run on the server.
- **It lets a retry run what never ran.** A failure that leaves the call safe
  to repeat reaches the callers waiting for it but is not kept, so asking again
  calls the tool again. That covers a call that never left the queue (no server
  reachable, the pool closing, its timeout spent waiting), and an idempotent
  call that lost its connection or timed out.
- **One process's answers cannot hold up another's.** A request from a
  process index with no reply queue is refused on arrival, with no tool call
  made for it. If a reply queue fails, the answers for that process are lost,
  and every other process still gets its own, and the message that the service
  has stopped.
- **It reports.** `serve` returns how many requests it received, how many
  calls it made, the repeats, the failures it did not keep (`retryable`), the
  requests it refused (`unknown_sources`), the answers it could not deliver
  (`undelivered`), and the outcomes per tool.
  `listener(uid, tool, received, called, answered, ok)` receives each call's
  times, for whoever keeps them.

Requests and answers are pickled. That is safe only because both ends are
processes of the same job: the queues must not be reachable from outside it.
A message the service cannot unpickle is skipped, and its callers fail at their
deadline.

## What a call returns

The service turns each MCP result into the value the caller gets:

- the structured content, if the result has some: its `result` field when
  that is all there is, as a server sends for a tool annotated to return a
  list or a string;
- otherwise the text: each text block parsed as JSON, or `{"text": text}` if
  it is not JSON, and a list of them if there are several;
- `None` for a success with no content, or only empty text: the tool ran and
  returned nothing, which is not a failure;
- `ToolServiceError` with the server's text for a result marked as an error,
  and for a success whose only content the agent could not read, such as an
  image.

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
- **Its errors say whether the call may have run.** Each error the pool
  raises for a call has `may_have_run`: True once a client had taken the call,
  False when it failed in the queue. A timeout is an `McpCallTimeout`, a
  `TimeoutError`.

`close()` stops intake, lets calls finish for up to `drain` seconds, fails the
rest with `McpPoolClosed`, and never raises. `pool.stats()` returns the pool's
state, its counters and its last errors.

## Completed calls at campaign scale

The default service keeps one compact outcome and payload per uid with a final
answer. For campaigns whose answers exceed the service's RAM budget, pass a
mutable mapping as `answer_store`. For example, open a Python `shelve` on local
disk for the lifetime of `serve`. Give each campaign and service shard its own
shelf, and route every retry of a uid to its original shard. Reusing that
shelf lets the service replay answers already recorded there. Storage grows
with the number and size of distinct answers. Budget one mapping lookup per
new uid and one write per final answer. Disk latency affects the service loop.

The store holds only final answers, as the service keeps them in memory: a
failure that leaves the call safe to repeat is not written, so a campaign that
resumes with the same shelf runs that call again. A shelf unpickles what it
reads, so keep it where only the campaign can write.

A write to a shelf is not on disk at once. Before Python 3.13, `shelve` uses
`dbm.gnu`, `dbm.ndbm` or `dbm.dumb`, whichever is installed, and none of them
syncs each write. Call the shelf's `sync()` from time to time, for example from
`listener` every few thousand calls, and close it when `serve` returns, so that
a crash of the service loses few answers. From Python 3.13, `shelve` uses
`dbm.sqlite3` when it is available, which commits each write.

The store cannot settle a crash between a remote side effect and recording its
answer. A non-idempotent tool that needs safe retries across that interval
requires deduplication at the tool server.
