# Miscellaneous 

## Multiple LLM Providers

Multiple LLM providers (e.g., OpenRouter, Ollama) through unified interface.

## Dynamic Runtime Graph Creation

**Location**: `src/flowgentic/langGraph/dynamic_graph.py`

Flowgentic provides a `DynamicGraph` base class that enables runtime modification of LangGraph workflows. This allows you to:

- **EXPAND**: Add new nodes to a running graph
- **REDUCE**: Remove nodes from a graph at runtime
- **UPDATE**: Replace node implementations dynamically
- **Custom Edges**: Override edge logic for conditional routing, parallel execution, or complex topologies
- **Flexible Initialization**: Choose which nodes to activate initially (default: all nodes)

All nodes are fully integrated with flowgentic's AsyncFlow system, ensuring proper async execution, fault tolerance, and telemetry.

### Key Features

- **Reusable Base Class**: Abstract base class with all modification logic built-in
- **Two Abstract Methods**: Only implement `_register_available_nodes()` and `_get_state_schema()`
- **Override `_add_edges()`**: Customize graph topology (sequential by default)
- **Full AsyncFlow Integration**: All nodes decorated with `@asyncflow` for proper async handling

### Quick Example

```python
from flowgentic.langGraph.dynamic_graph import DynamicGraph
from flowgentic.langGraph.execution_wrappers import AsyncFlowType

class MyGraph(DynamicGraph):
    def _register_available_nodes(self):
        @self.agents_manager.execution_wrappers.asyncflow(
            flow_type=AsyncFlowType.FUNCTION_TASK
        )
        async def process_node(state):
            # Your node logic
            return updated_state
        
        self.available_nodes = {"process": process_node}
    
    def _get_state_schema(self):
        return GraphState

# Usage
async with LangraphIntegration(backend=backend) as agents_manager:
    graph = MyGraph(agents_manager, initial_active_nodes=["node_a", "node_b"])
    await graph.rebuild_graph()
    result = await graph.run()
    
    # Modify at runtime
    await graph.expand_graph("node_c")
    await graph.reduce_graph("node_a")
    await graph.update_graph("node_b", "node_d")
```

### Use Cases

- **Adaptive Workflows**: Modify pipelines based on intermediate results
- **A/B Testing**: Swap implementations at runtime to test different approaches
- **Feature Flags**: Enable/disable processing steps dynamically
- **Progressive Enhancement**: Start minimal and add capabilities based on load
- **Error Recovery**: Route around failing components
- **Hot Swapping**: Update business logic without restart

### Documentation

- **Base Class**: `src/flowgentic/langGraph/dynamic_graph.py`
- **Complete Guide**: `examples/langgraph-integration/miscellaneous/README_DYNAMIC_GRAPH.md`
- **Example**: `examples/langgraph-integration/miscellaneous/runtime-graph-creation.py`
  - Sequential flow with dynamic modification
  - Custom initial active nodes
  - Conditional routing example with custom `_add_edges()`
