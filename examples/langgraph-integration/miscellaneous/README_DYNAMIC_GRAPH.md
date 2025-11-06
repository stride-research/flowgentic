# Dynamic Graph Base Class - Comprehensive Guide

## 🎯 Overview

The `DynamicGraph` base class provides a reusable foundation for creating LangGraphs that can be modified at runtime. It handles all the boilerplate for dynamic graph management while allowing subclasses to customize node logic and edge topology.

## 📍 Location

**Base Class**: `src/flowgentic/langGraph/dynamic_graph.py`
**Example**: `examples/langgraph-integration/miscellaneous/runtime-graph-creation.py`

## ✨ Key Features

1. **Runtime Modification**: EXPAND, REDUCE, and UPDATE operations
2. **Flexible Initialization**: Choose which nodes to activate initially
3. **Custom Edge Logic**: Override `_add_edges()` for non-sequential flows
4. **Full AsyncFlow Integration**: All nodes execute through flowgentic's async system
5. **Comprehensive Logging**: Every operation tracked with visual indicators

## 🏗️ Architecture

```python
DynamicGraph (Abstract Base Class)
├── __init__(agents_manager, initial_active_nodes)
├── rebuild_graph()  # Orchestrates graph reconstruction
│   ├──  _add_nodes()     # Adds nodes to workflow
│   └── _add_edges()      # Connects nodes (can be overridden)
├── expand_graph()   # Add nodes at runtime
├── reduce_graph()   # Remove nodes at runtime  
├── update_graph()   # Replace nodes at runtime
└── run()            # Execute the graph

Abstract Methods (must implement):
├── _register_available_nodes()  # Define your nodes
└── _get_state_schema()          # Return state TypedDict
```

## 📝 Basic Usage

### Step 1: Define Your State Schema

```python
from typing import Annotated, List, TypedDict
from langgraph.graph.message import add_messages

class GraphState(TypedDict):
    messages: Annotated[List[str], add_messages]
    execution_path: List[str]
    operation_count: int
```

### Step 2: Create Your Graph Class

```python
from flowgentic.langGraph.dynamic_graph import DynamicGraph
from flowgentic.langGraph.execution_wrappers import AsyncFlowType

class MyDynamicGraph(DynamicGraph):
    """Your custom dynamic graph implementation."""
    
    def _register_available_nodes(self) -> None:
        """Register all available nodes."""
        
        @self.agents_manager.execution_wrappers.asyncflow(
            flow_type=AsyncFlowType.FUNCTION_TASK
        )
        async def process_node(state: GraphState) -> GraphState:
            """Your node logic here."""
            await asyncio.sleep(0.1)  # Async work
            return {
                "messages": ["Processed"],
                "execution_path": state.get("execution_path", []) + ["process"],
                "operation_count": state.get("operation_count", 0) + 1
            }
        
        self.available_nodes = {
            "process": process_node,
            # Add more nodes...
        }
    
    def _get_state_schema(self) -> type:
        """Return the state schema."""
        return GraphState
```

### Step 3: Use Your Graph

```python
async with LangraphIntegration(backend=backend) as agents_manager:
    # Create with default (all nodes active)
    graph = MyDynamicGraph(agents_manager)
    
    # Or specify initial nodes
    graph = MyDynamicGraph(
        agents_manager,
        initial_active_nodes=["node_a", "node_b"]
    )
    
    # Build and run
    await graph.rebuild_graph()
    result = await graph.run()
    
    # Modify at runtime
    await graph.expand_graph("node_c")
    await graph.reduce_graph("node_a")
    await graph.update_graph("node_b", "node_d")
```

## 🎨 Customization Options

### 1. Initial Active Nodes

Control which nodes are active when the graph is created:

```python
# Option A: Activate all registered nodes (default)
graph = MyDynamicGraph(agents_manager)

# Option B: Activate specific subset
graph = MyDynamicGraph(
    agents_manager,
    initial_active_nodes=["node_c", "node_d", "node_e"]
)
```

### 2. Custom Edge Logic

Override `_add_edges()` for non-sequential flows:

#### Sequential Edges (Default)

The base class provides sequential flow by default:
```
node_1 → node_2 → node_3 → END
```

#### Conditional Edges

```python
class ConditionalGraph(DynamicGraph):
    def _add_edges(self, workflow):
        """Custom conditional routing."""
        from langgraph.graph import END
        
        def route_function(state):
            if state.get("should_optimize"):
                return "optimize_node"
            return "standard_node"
        
        # Conditional edge
        workflow.add_conditional_edges("decision_node", route_function)
        
        # Both paths to END
        workflow.add_edge("standard_node", END)
        workflow.add_edge("optimize_node", END)
```

#### Parallel Edges

```python
def _add_edges(self, workflow):
    """Parallel execution pattern."""
    from langgraph.graph import END, START
    
    # Start triggers all nodes in parallel
    for node in ["node_a", "node_b", "node_c"]:
        if node in self.active_node_names:
            workflow.add_edge(START, node)
            workflow.add_edge(node, END)
```

#### Complex Topology

```python
def _add_edges(self, workflow):
    """Custom complex flow."""
    from langgraph.graph import END
    
    # Define your custom topology
    workflow.add_edge("input_node", "process_node")
    workflow.add_conditional_edges(
        "process_node",
        self.route_after_process,
        {"validate": "validate_node", "error": "error_node"}
    )
    workflow.add_edge("validate_node", "output_node")
    workflow.add_edge("error_node", "retry_node")
    workflow.add_edge("retry_node", "process_node")
    workflow.add_edge("output_node", END)
```

### 3. Custom Run Logic

Override `run()` to add custom behavior:

```python
async def run(self, initial_state: Optional[Dict] = None):
    """Custom execution with preprocessing/postprocessing."""
    
    # Preprocessing
    if initial_state is None:
        initial_state = self._create_default_state()
    
    # Validation
    self._validate_state(initial_state)
    
    # Call parent's run
    result = await super().run(initial_state)
    
    # Postprocessing
    if result:
        self._log_detailed_results(result)
        self._update_metrics(result)
    
    return result
```

## 📊 Complete Example

Here's a complete example showing all features:

```python
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, List, Optional, TypedDict
from radical.asyncflow import ConcurrentExecutionBackend
from langgraph.graph.message import add_messages

from flowgentic.langGraph.dynamic_graph import DynamicGraph
from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.utils.logger.logger import Logger

# Initialize logging
Logger(colorful_output=True, logger_level="INFO")
logger = logging.getLogger(__name__)


# 1. Define State Schema
class ProcessingState(TypedDict):
    messages: Annotated[List[str], add_messages]
    execution_path: List[str]
    operation_count: int
    error_count: int


# 2. Create Graph Implementation
class ProcessingPipeline(DynamicGraph):
    """Data processing pipeline with dynamic nodes."""
    
    def __init__(
        self,
        agents_manager: LangraphIntegration,
        initial_active_nodes: Optional[List[str]] = None
    ):
        # Default to core processing nodes
        if initial_active_nodes is None:
            initial_active_nodes = ["validate", "transform", "save"]
        
        super().__init__(agents_manager, initial_active_nodes)
    
    def _register_available_nodes(self) -> None:
        """Register all processing nodes."""
        
        @self.agents_manager.execution_wrappers.asyncflow(
            flow_type=AsyncFlowType.FUNCTION_TASK
        )
        async def validate(state: ProcessingState) -> ProcessingState:
            """Validate input data."""
            logger.info("✓ Validating data")
            await asyncio.sleep(0.1)
            return {
                "messages": ["Data validated"],
                "execution_path": state.get("execution_path", []) + ["validate"],
                "operation_count": state.get("operation_count", 0) + 1,
                "error_count": state.get("error_count", 0)
            }
        
        @self.agents_manager.execution_wrappers.asyncflow(
            flow_type=AsyncFlowType.FUNCTION_TASK
        )
        async def transform(state: ProcessingState) -> ProcessingState:
            """Transform data."""
            logger.info("⚙️  Transforming data")
            await asyncio.sleep(0.1)
            return {
                "messages": ["Data transformed"],
                "execution_path": state.get("execution_path", []) + ["transform"],
                "operation_count": state.get("operation_count", 0) + 1,
                "error_count": state.get("error_count", 0)
            }
        
        @self.agents_manager.execution_wrappers.asyncflow(
            flow_type=AsyncFlowType.FUNCTION_TASK
        )
        async def enrich(state: ProcessingState) -> ProcessingState:
            """Enrich data with additional information."""
            logger.info("✨ Enriching data")
            await asyncio.sleep(0.1)
            return {
                "messages": ["Data enriched"],
                "execution_path": state.get("execution_path", []) + ["enrich"],
                "operation_count": state.get("operation_count", 0) + 1,
                "error_count": state.get("error_count", 0)
            }
        
        @self.agents_manager.execution_wrappers.asyncflow(
            flow_type=AsyncFlowType.FUNCTION_TASK
        )
        async def save(state: ProcessingState) -> ProcessingState:
            """Save processed data."""
            logger.info("💾 Saving data")
            await asyncio.sleep(0.1)
            return {
                "messages": ["Data saved"],
                "execution_path": state.get("execution_path", []) + ["save"],
                "operation_count": state.get("operation_count", 0) + 1,
                "error_count": state.get("error_count", 0)
            }
        
        self.available_nodes = {
            "validate": validate,
            "transform": transform,
            "enrich": enrich,
            "save": save,
        }
    
    def _get_state_schema(self) -> type:
        return ProcessingState


# 3. Use the Pipeline
async def main():
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor(max_workers=4))
    
    async with LangraphIntegration(backend=backend) as agents_manager:
        # Create with default nodes
        pipeline = ProcessingPipeline(agents_manager)
        await pipeline.rebuild_graph()
        
        # Run initial pipeline
        logger.info("Running basic pipeline")
        result = await pipeline.run()
        logger.info(f"Path: {' → '.join(result['execution_path'])}")
        
        # Add enrichment step dynamically
        logger.info("\nAdding enrichment step")
        await pipeline.expand_graph("enrich", position=2)
        result = await pipeline.run()
        logger.info(f"Path: {' → '.join(result['execution_path'])}")
        
        # Remove validation for fast processing
        logger.info("\nEnabling fast mode (no validation)")
        await pipeline.reduce_graph("validate")
        result = await pipeline.run()
        logger.info(f"Path: {' → '.join(result['execution_path'])}")


if __name__ == "__main__":
    asyncio.run(main())
```

## 🔍 Advanced Patterns

### Pattern 1: Feature Flags

```python
class FeatureFlaggedGraph(DynamicGraph):
    def __init__(self, agents_manager, feature_flags: Dict[str, bool]):
        # Activate nodes based on feature flags
        active_nodes = [
            node for node, enabled in feature_flags.items()
            if enabled
        ]
        super().__init__(agents_manager, active_nodes)
```

###Pattern 2: A/B Testing

```python
class ABTestGraph(DynamicGraph):
    def __init__(self, agents_manager, variant: str):
        if variant == "A":
            initial_nodes = ["process_v1", "output"]
        else:  # variant B
            initial_nodes = ["process_v2", "output"]
        
        super().__init__(agents_manager, initial_nodes)
```

### Pattern 3: Progressive Enhancement

```python
async def progressive_pipeline():
    # Start minimal
    pipeline = MyGraph(agents_manager, ["core_process"])
    
    # Add capabilities based on load/performance
    if low_load():
        await pipeline.expand_graph("quality_check")
    
    if has_capacity():
        await pipeline.expand_graph("ml_enhancement")
```

## 📚 API Reference

### DynamicGraph Methods

#### `__init__(agents_manager, initial_active_nodes=None)`
- **agents_manager**: LangraphIntegration instance
- **initial_active_nodes**: Optional list of node names to activate (defaults to all)

#### `async rebuild_graph()`
Rebuild the graph with current active nodes. Called automatically after modifications.

#### `async expand_graph(new_node, position=None)`
Add a node at runtime.
- **new_node**: Name of node to add
- **position**: Optional insertion position (None = append)
- **Returns**: True if successful

#### `async reduce_graph(node_to_remove)`
Remove a node at runtime.
- **node_to_remove**: Name of node to remove
- **Returns**: True if successful

#### `async update_graph(old_node, new_node)`
Replace one node with another.
- **old_node**: Node to replace
- **new_node**: Replacement node
- **Returns**: True if successful

#### `async run(initial_state=None)`
Execute the graph.
- **initial_state**: Starting state (optional)
- **Returns**: Final state after execution

### Abstract Methods

#### `_register_available_nodes()`
Register all available nodes. Must populate `self.available_nodes` dict.

#### `_get_state_schema()`
Return the TypedDict class for state schema.

### Overridable Methods

#### `_add_nodes(workflow)`
Add nodes to workflow (rarely needs override).

#### `_add_edges(workflow)`
Add edges to workflow (override for custom topology).

## ✅ Best Practices

1. **Node Registration**: Always decorate nodes with `@asyncflow`
2. **State Immutability**: Return new state dicts, don't mutate
3. **Error Handling**: Let fault tolerance handle retries
4. **Logging**: Use logger for visibility into operations
5. **Initial Nodes**: Provide sensible defaults in `__init__`
6. **Edge Logic**: Document custom edge behavior clearly

## 🎓 Learn More

- **Base Implementation**: `src/flowgentic/langGraph/dynamic_graph.py`
- **Sequential Example**: `DynamicFlowgenticGraph` in runtime-graph-creation.py
- **Conditional Example**: `ConditionalFlowGraph` in runtime-graph-creation.py
- **LangGraph Docs**: https://langchain-ai.github.io/langgraph/

## 🐛 Troubleshooting

**Problem**: "Node X not found in available nodes"
- **Solution**: Make sure node is registered in `_register_available_nodes()`

**Problem**: Conditional edges not working
- **Solution**: Check that routing function returns valid node names

**Problem**: Graph not rebuilding
- **Solution**: Ensure you called `await rebuild_graph()` after modifications

**Problem**: State not updating
- **Solution**: Make sure nodes return dicts that match state schema

