# Checkpointer

An agent that suspends while a task runs leaves its state in LangGraph's
checkpointer, and an HPC campaign suspends thousands of agents at a time.
`flowgentic.langgraph.KeyValueSaver` keeps their checkpoints in a key-value
store in the allocation's memory: RaDEX, on a Dragon distributed dictionary,
in a run; a dictionary in the process in tests.

```python
from flowgentic.langgraph import KeyValueSaver
from flowgentic.store import MemoryStore, RadexStore

saver = KeyValueSaver(MemoryStore())                    # tests, local runs
saver = KeyValueSaver(RadexStore.attach(descriptor))    # a Dragon dictionary

graph = builder.compile(checkpointer=saver)
await graph.ainvoke(state, {"configurable": {"thread_id": agent_id}}, durability="exit")
```

`flowgentic.store` needs no extra. `KeyValueSaver` currently imports through the integration package and needs both the
`langgraph` and `asyncflow` extras. `RadexStore` needs RaDEX's Python client, which is built from source
with its C++ core: see [RaDEX](https://github.com/radical-cybertools/radex).
Tensors, in either store, need NumPy.

## Stores

A `KeyValueStore` holds picklable values under string keys, with four
methods: `get`, `put`, `delete` and `contains`. Two come with Flowgentic.

- `RadexStore.attach(descriptor)` returns this process's one client for the
  dictionary that `descriptor` names. Each client costs Dragon channels, so a
  process attaches once and every caller in it shares the client.
- `MemoryStore()` is a dictionary in the process. It pickles values as RaDEX
  does, so callers share no objects.

Both also store float64 tensors (`put_tensor`, `get_tensor`), so tools can
share arrays through the same client as the checkpointer.

Values are pickled, so a store must be trusted by every process that reads
it. That holds for a store inside one job's allocation.

## What the saver keeps

Each store call is a round trip that blocks the event loop, so the saver keeps
calls few. For thread `t` and checkpoint namespace `ns`:

| Key | Holds |
| --- | --- |
| `lg/{t}/{ns}/head` | the newest checkpoint, with its metadata and parent id |
| `lg/{t}/{ns}/w` | pending writes grouped by checkpoint id |
| `lg/{t}/{ns}/c/{id}` | an older checkpoint, with its writes, when `keep_last` > 1 |
| `lg/{t}/{ns}/index` | the older checkpoints' ids, oldest first |
| `lg/{t}/namespaces` | the thread's namespaces other than the root |

- **It keeps the newest checkpoint only, by default.** Each checkpoint holds
  the agent's whole history, so keeping every one grows with the square of a
  run's length. With `keep_last=1` a checkpoint is one `put`, which replaces
  the previous one, and nothing is deleted. `keep_last=n` keeps the newest
  `n`, for time travel over the last steps.
- **A root-namespace suspension with full channel values costs six store calls.**
  With LangGraph's `exit` durability, an
  agent that suspends while its task runs writes its checkpoint and its
  pending writes, and reads both back when it resumes. Saving writes also
  reads the current head and the existing writes so a retry preserves them.
- LangGraph delta channels recover their history from a snapshot and the writes
  since that snapshot. The saver carries this information into the retained
  checkpoint before its parent expires. These checkpoints also read the parent.
- **One process writes each thread.** The store has no atomic update, so the
  saver reads, changes and writes back the pending writes. That is safe
  because one process owns the thread, and updates in that process take a
  lock shared by savers for the thread.
- **`list` needs a thread id**, because RaDEX cannot list its keys.

`saver.stats()` returns how many checkpoints and writes the saver has put, how
many checkpoints it has read, and their mean size in bytes.

## Reducing store traffic on an owned shard

When a single process owns a shard, its savers can share a bounded
`WriteThroughStore` over the backing store. For example:

```python
from flowgentic.store import RadexStore, WriteThroughStore

owned = WriteThroughStore(RadexStore.attach(descriptor), max_bytes=32 << 20)
saver = KeyValueSaver(owned)
```

Route every read, update and delete for those keys through the same wrapper.
To hand off the shard, stop the outgoing writer and give the receiving writer
a new wrapper with an empty cache. Independent readers or writers that go
around the wrapper can leave its entries stale. Use the ordinary saver on a
shared partition without exclusive ownership.

Every write reaches the backing store before returning. The cache retains
up to `max_bytes` of pickled values and `max_entries` keys (16,384 by
default). The keys, Python bookkeeping and transient serialization buffers
also consume memory. Size it for the concurrently active agents. Oversized
values bypass retention and evicted values are read from the backing store.

For a new root-namespace checkpoint with ordinary channels, writing the
checkpoint, saving one batch of pending writes and reading it to resume uses
three backing calls when the two keys stay resident. Those calls are two puts
and the first lookup of pending writes. Once both keys are warm, the same
sequence uses two puts. The ordinary saver uses six calls. Initial graph
lookup and the final checkpoint after resuming are separate operations.
Caching adds local serialization work, so benchmark the backing-store latency
and the distance between suspension and resume on the intended allocation.

To measure the actual Dragon path, use an active allocation and save its DDict's
serialized descriptor in a local file. In the environment that has the RaDEX
client and Flowgentic's integration dependencies installed, run:

```bash
uv run --no-sync python benchmarks/checkpoint_resume.py --backend radex --descriptor-file ddict.txt --agents 1000 --window 256
```

Repeat with `--cache-mib 32` and the same window and payload size. The benchmark
suspends and resumes real LangGraph threads, then verifies the saved result
directly against the selected backend. Its operation counts include the initial
lookup and final checkpoint. Verification and cleanup run outside the timed
interval. Each run uses and cleans up its own thread IDs. `--backend memory`
is a local control; results identify the backend used.
