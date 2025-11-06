# Dynamic Graph

Runtime graph modification for LangGraph workflows. Modify graph topology during execution without rebuilding the entire application.

## Core Operations

- **EXPAND**: Add nodes to active graph
- **REDUCE**: Remove nodes from active graph  
- **UPDATE**: Replace one node with another
- **REGISTER**: Add new node functions at runtime

## Implementation

Subclass `DynamicGraph` and implement two required methods:

```python
from flowgentic.langGraph.dynamic_graph import DynamicGraph
from typing import TypedDict

class GraphState(TypedDict):
    messages: list
    operations: int

class MyDynamicGraph(DynamicGraph):
    def _register_available_nodes(self) -> None:
        """Register all node functions."""
        @self.agents_manager.execution_wrappers.asyncflow(
            flow_type=AsyncFlowType.FUNCTION_TASK
        )
        async def node_a(state: GraphState) -> GraphState:
            state["messages"].append("Node A executed")
            return state
        
        self.available_nodes["node_a"] = node_a
    
    def _get_state_schema(self) -> type:
        """Return state TypedDict."""
        return GraphState
```

## Usage

### Initialize with Active Nodes

```python
# Start with subset of registered nodes
graph = MyDynamicGraph(
    agents_manager=agents_manager,
    initial_active_nodes=["node_a", "node_b"]
)
await graph.rebuild_graph()
```

### Expand Graph

```python
# Add pre-registered node
await graph.expand_graph("node_c")  # Appends to end
await graph.expand_graph("node_d", position=1)  # Insert at index
```

### Reduce Graph

```python
# Remove node from active graph
await graph.reduce_graph("node_b")
```

### Update Graph

```python
# Replace node
await graph.update_graph(old_node="node_c", new_node="node_d")
```

### Register Runtime Nodes

```python
# Register completely new node at runtime
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.FUNCTION_TASK
)
async def node_f(state: GraphState) -> GraphState:
    state["messages"].append("Runtime node")
    return state

graph.register_node("node_f", node_f)
await graph.expand_graph("node_f", position=1)
```

### Execute Graph

```python
result = await graph.run(initial_state={"messages": [], "operations": 0})
print(result["messages"])
```

## Custom Edge Logic

Override `_add_edges()` for non-sequential flows:

```python
def _add_edges(self, workflow: StateGraph) -> None:
    """Custom conditional routing."""
    
    def route_decision(state: GraphState) -> str:
        return "optimize" if state["operations"] > 5 else "standard"
    
    workflow.add_conditional_edges(
        "decision",
        route_decision,
        {"standard": "standard", "optimize": "optimize"}
    )
    workflow.add_edge("standard", END)
    workflow.add_edge("optimize", END)
```

## Example Output

<details>
<summary>Execution logs</summary>

```
PART 1: Sequential Graph with Custom Initial Nodes
Creating graph with only nodes A and B active initially
✅ Activated 2 nodes: ['node_a', 'node_b']
🔄 Rebuilding graph with nodes: ['node_a', 'node_b']
▶️  Executing graph: node_a -> node_b

📈 RESULT: Initial: A -> B
Execution Path: node_a → node_b
Operations: 2

--- EXPAND: Add Node C (pre-registered) ---
➕ EXPAND: Adding node 'node_c' at position end
🔄 Rebuilding graph with nodes: ['node_a', 'node_b', 'node_c']
▶️  Executing graph: node_a -> node_b -> node_c

📈 RESULT: After: A -> B -> C
Operations: 3

--- REDUCE: Remove Node B ---
➖ REDUCE: Removing node 'node_b'
🔄 Rebuilding graph with nodes: ['node_a', 'node_c']
▶️  Executing graph: node_a -> node_c

📈 RESULT: After: A -> C
Operations: 2

--- UPDATE: Replace C with D ---
🔄 UPDATE: Replacing 'node_c' with 'node_d'
▶️  Executing graph: node_a -> node_d

📈 RESULT: After: A -> D
Operations: 2

--- REGISTER NEW NODE: Add node_f (not pre-registered) ---
✅ Registered new node: 'node_f'
➕ EXPAND: Adding node 'node_f' at position 1
▶️  Executing graph: node_a -> node_f -> node_d

📈 RESULT: After: A -> F (new) -> D
Operations: 3
```

</details>

## Default Behavior

- **Edges**: Sequential (node[i] → node[i+1] → END)
- **Entry Point**: First node in active list
- **Execution**: AsyncFlow-backed, fault-tolerant

## Use Cases

- Adaptive workflows based on runtime conditions
- A/B testing different node configurations
- Progressive pipeline expansion
- Dynamic optimization strategies
- Conditional branch activation

