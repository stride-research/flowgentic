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

`flowgentic.store` needs no extra. `KeyValueSaver` needs the `langgraph`
extra. `RadexStore` needs RaDEX's Python client, which is built from source
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
| `lg/{t}/namespaces` | the thread's namespaces other than the root, each with its parent's checkpoint id |

- **It keeps the newest checkpoint only, by default.** Each checkpoint holds
  the agent's whole history, so keeping every one grows with the square of a
  run's length. With `keep_last=1` a checkpoint is one `put`, which replaces
  the previous one, and nothing is deleted. `keep_last=n` keeps the newest
  `n`, for time travel over the last steps.
- **A suspension costs six store calls,** in the root namespace with ordinary
  channels. With LangGraph's `exit` durability, an agent that suspends while
  its task runs writes its checkpoint and its pending writes, and reads both
  back when it resumes. Saving pending writes also reads the newest checkpoint
  and the writes already saved, so that a retried save, or another saver in
  the process, adds to those writes instead of replacing them.
- LangGraph delta channels recover their history from a snapshot and the writes
  since that snapshot. The saver carries this information into the retained
  checkpoint before its parent expires. These checkpoints also read the parent.
- **One process writes each thread.** The store has no atomic update, so the
  saver reads, changes and writes back the pending writes. That is safe
  because one process owns the thread, and updates in that process take a
  lock shared by savers for the thread.
- **A subgraph's checkpoints go once its parent has moved on.** Each run of a
  subgraph has a namespace of its own, so a graph that runs a suspending
  subgraph in a loop, as a supervisor does, would leave one behind on every
  turn. When a subgraph's namespace is first saved, the saver drops its
  siblings that ran under a checkpoint of the parent's older than every one
  the parent keeps, with their own subgraphs. The parent can no longer resume
  them. A loop then keeps the subgraphs of its last step or two, however long
  it runs. This costs a read of the parent's newest checkpoint (and of its
  index, above `keep_last=1`) when a subgraph's namespace is first saved, and
  nothing in the root namespace.
- **`list` needs a thread id**, because RaDEX cannot list its keys.
- **A finished agent's thread leaves the store** when the agent host's
  `GraphRunner` runs it: the runner deletes the thread once the agent
  finishes. The store then grows with the agents live at once, not with the
  campaign. `delete_thread` removes a thread, its namespaces included; it
  takes a few store calls per namespace.

`saver.stats()` returns how many checkpoints and writes the saver has put, how
many checkpoints it has read, and their mean size in bytes.
