# Mutable graph

`MutableGraph` (`flowgentic.langGraph.mutable_graph`) is a base class for a
LangGraph graph whose nodes change while the application runs. It keeps a set
of registered node functions and a list of active nodes, and recompiles the
graph after each change.

The code on this page comes from
`examples/langgraph-integration/miscellaneous/mutable-graph-creation.py`, which
runs offline and is tested in `tests/integration/test_feature_examples.py`.

## Define the graph

A subclass implements two methods: `_register_available_nodes`, which fills
`self.available_nodes` with node functions, and `_get_state_schema`, which
returns the state schema. The example's nodes are `FUNCTION_TASK` functions,
so each runs as an AsyncFlow task:

```python
--8<-- "examples/langgraph-integration/miscellaneous/mutable-graph-creation.py:subclass"
```

```python
--8<-- "examples/langgraph-integration/miscellaneous/mutable-graph-creation.py:register-nodes"
```

The example's state schemas:

```python
--8<-- "examples/langgraph-integration/miscellaneous/mutable-graph-creation.py:state-schemas"
```

## Change it at runtime

The constructor takes the integration and the initially active nodes, which
must be registered. `rebuild_graph()` compiles the graph, and `run(state)`
invokes it:

```python
--8<-- "examples/langgraph-integration/miscellaneous/mutable-graph-creation.py:operations"
```

| Method | Effect |
| --- | --- |
| `expand_graph(name, position=None)` | Adds a registered node at the end, or at `position` |
| `reduce_graph(name)` | Removes an active node |
| `update_graph(old, new)` | Replaces an active node with a registered one |
| `register_node(name, function)` | Registers a new node function; it is not active until added |

`expand_graph`, `reduce_graph` and `update_graph` rebuild the graph and return
`True`. They change nothing and return `False` when a node is not registered
or, for `reduce_graph` and `update_graph`, not active. `expand_graph` and
`update_graph` also return `False` when the new node is already active. (In
0.3.0, `update_graph` accepted it, listed it twice, and the rebuild raised
LangGraph's `ValueError`.) `register_node` returns `False` when the name is taken. A
function registered at runtime is added like any other node:

```python
--8<-- "examples/langgraph-integration/miscellaneous/mutable-graph-creation.py:register-runtime"
```

## Edges

By default the active nodes run in sequence, from the first to `END`. To route
differently, override `_add_edges(workflow)`. The example's
`ConditionalFlowGraph` routes on a flag that its decision node sets:

```python
--8<-- "examples/langgraph-integration/miscellaneous/mutable-graph-creation.py:conditional-edges"
```

LangGraph keeps only the state keys that the schema declares, and reads a
routing function's input through its type annotation. The flag must be in the
graph's schema and in the router's annotation, which is why the example
declares `ConditionalState`.

## Run the example

```bash
uv run --extra examples python -m examples.langgraph-integration.miscellaneous.mutable-graph-creation
```

Among its log lines, the run prints each result's path through the graph:

```text
Execution Path: node_a → node_b
Execution Path: node_a → node_b → node_c
Execution Path: node_a → node_c
Execution Path: node_a → node_d
Execution Path: node_a → node_f → node_d
🔀 Conditional Result 1: decision → standard
🔀 Conditional Result 2: decision → optimize
```

## API

[`flowgentic.langGraph.mutable_graph`](../api/flowgentic/langGraph/mutable_graph.md)
