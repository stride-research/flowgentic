# Memory

`flowgentic.langGraph.memory` keeps a bounded, in-process message history for
an agent and trims it when it grows past a limit. `LangraphIntegration`
creates one as `integration.memory_manager`; an application can also create a
`MemoryManager` itself. Nothing is persisted: the history lives as long as the
manager object.

The code on this page comes from `tests/unit/test_memory.py`.

## Configure

`MemoryConfig` sets the limit and the trimming strategy:

| Parameter | Default | Effect |
| --- | --- | --- |
| `max_short_term_messages` | 50 | Trim when the history holds more messages than this |
| `short_term_strategy` | `"trim_last"` | How to trim; see below |
| `enable_summarization` | `False` | Must be `True`, with a chat model, for `"summarize"` |
| `context_window_buffer` | 10 | Reported by `get_memory_health()`; does not change trimming |
| `memory_update_threshold` | 5 | Reported by `get_memory_health()`; does not change trimming |
| `summarization_batch_size` | 10 | Not used |

## Trimming strategies

Every strategy keeps all system messages, then fills the rest of the limit:

| Strategy | Keeps |
| --- | --- |
| `"trim_last"` | The most recent messages |
| `"trim_middle"` | The oldest and the most recent messages, half each, dropping the middle |
| `"importance_based"` | The highest-scoring messages, ordered by score. A message scores 2.0 if it is a system message, 1.5 if it is an AI message with tool calls, 1.2 if it is a human message and 1.0 otherwise, plus up to 1.0 for length (one point per 1,000 characters) |
| `"summarize"` | The most recent messages, plus one AI message that summarizes the older ones. It needs a chat model and `enable_summarization=True`, and otherwise trims as `"trim_last"` does |

With `"trim_last"` and a limit of 2, the system message and the latest message
remain:

```python
--8<-- "tests/unit/test_memory.py:trim-last"
```

With `"summarize"`, the manager calls the chat model's `invoke` once to
summarize the older messages:

```python
--8<-- "tests/unit/test_memory.py:summarize"
```

The summary is a synchronous model call, made while the messages are added.
If the call fails, the older messages are dropped without a summary.

## Add and retrieve interactions

`MemoryManager(config, llm=None)` wraps the short-term history with an async
interface:

```python
--8<-- "tests/unit/test_memory.py:memory-manager"
```

- `add_interaction(user_id, messages, metadata=None)` adds the messages and
  returns the message count, the interaction count and statistics.
- `get_relevant_context(user_id, query=None)` returns `short_term` (the whole
  history), `memory_stats` and `relevant_messages`. Without a query,
  `relevant_messages` holds the last 10 messages. With a query, it holds up to
  10: the 8 that best match the query's words, weighted by importance and
  recency, and the 3 most recent.
- `get_short_term_messages()`, `clear_short_term_memory()`,
  `consolidate_memory()` and `get_memory_health()` read, clear, re-score and
  report on the history.

`user_id` and `metadata` are accepted but not used: one manager holds one
history, shared by every user ID.

## In the integration

`integration.memory_manager` is a `LangraphMemoryManager` with the default
`MemoryConfig` and no chat model. It offers the same methods as
`MemoryManager`, and `update_config(config)` replaces its configuration and
empties its history.

The example `examples/langgraph-integration/design_patterns/sequential/research_agent_memory/`
uses a `MemoryManager` with the `"importance_based"` strategy in a sequential
research graph. It needs a model provider and credentials.

## API

[`flowgentic.langGraph.memory`](../api/flowgentic/langGraph/memory.md)
