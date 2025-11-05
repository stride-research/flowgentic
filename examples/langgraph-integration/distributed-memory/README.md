# Distributed Memory with Dragon DDict

This example demonstrates how to use Flowgentic's distributed memory capabilities for HPC environments using Dragon DDict.

## Overview

Flowgentic provides a three-layer architecture for distributed memory:

1. **Layer 1: Dragon DDict Backends** (`distributed_memory.py`)
   - `DragonDDictStore`: Distributed key-value store for long-term memory
   - `DragonCheckpointSaver`: Distributed checkpoint persistence for graph state

2. **Layer 2: Memory Management** (`memory.py`)
   - `DistributedMemoryConfig`: Configuration for distributed memory
   - `DistributedMemoryManager`: Wrapper around Dragon implementations
   - Enhanced `LangraphMemoryManager`: Unified interface for local and distributed modes

3. **Layer 3: Integration** (`main.py`)
   - Enhanced `LangraphIntegration`: Automatic initialization and cleanup
   - Unified API regardless of backend

## Key Features

- **Graceful Degradation**: Automatically falls back to in-memory storage if Dragon is unavailable
- **Auto-Detection**: Can automatically detect HPC environments (SLURM, PBS, LSF, Dragon)
- **Flexible Configuration**: Support for local, distributed, and auto modes
- **Memory Isolation**: Separate namespaces for different users/threads
- **Checkpoint Persistence**: Distributed state persistence across nodes

## Usage Modes

### 1. Local Mode (In-Memory)

```python
from flowgentic.langGraph.memory import DistributedMemoryConfig, LangraphMemoryManager

distributed_config = DistributedMemoryConfig(mode="local")
memory_manager = LangraphMemoryManager(distributed_config=distributed_config)
```

### 2. Distributed Mode (Dragon DDict)

```python
distributed_config = DistributedMemoryConfig(
    mode="distributed",
    n_nodes=4,  # Number of nodes
    managers_per_node=2,  # Managers per node
    store_total_mem=10 * 1024**3,  # 10 GB for store
    checkpoint_total_mem=20 * 1024**3,  # 20 GB for checkpoints
)
memory_manager = LangraphMemoryManager(distributed_config=distributed_config)
```

### 3. Auto Mode (Environment Detection)

```python
distributed_config = DistributedMemoryConfig(mode="auto")
memory_manager = LangraphMemoryManager(distributed_config=distributed_config)
# Automatically uses distributed if HPC environment detected
```

## Running the Example

### Basic Usage

```bash
python distributed_memory_example.py
```

### With Dragon (HPC Environment)

```bash
# On a SLURM cluster
srun -N 4 python distributed_memory_example.py

# With Dragon runtime
dragon distributed_memory_example.py
```

## Configuration Options

### DistributedMemoryConfig Parameters

- `mode`: `"local"`, `"distributed"`, or `"auto"`
- `managers_per_node`: Number of Dragon managers per node (default: 1)
- `n_nodes`: Number of nodes for distribution (default: 1)
- `store_total_mem`: Total memory for store in bytes (default: 10 GB)
- `checkpoint_total_mem`: Total memory for checkpoints in bytes (default: 20 GB)
- `enable_store`: Enable distributed long-term memory store (default: True)
- `enable_checkpointer`: Enable distributed checkpoint saver (default: True)
- `fallback_to_local`: Fall back to local if Dragon unavailable (default: True)

## Integration with LangGraph

The distributed memory seamlessly integrates with LangGraph:

```python
from langgraph.graph import StateGraph
from flowgentic.langGraph.main import LangraphIntegration

# Configure memory
distributed_config = DistributedMemoryConfig(mode="auto")

# Create integration
async with LangraphIntegration(
    backend=backend,
    distributed_config=distributed_config
) as integration:
    # Build graph
    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    
    # Get distributed backends
    checkpointer = integration.memory_manager.get_checkpointer()
    store = integration.memory_manager.get_store()
    
    # Compile with distributed memory
    graph = builder.compile(checkpointer=checkpointer, store=store)
    
    # Run with persistent state
    result = graph.invoke(state, config={"configurable": {"thread_id": "123"}})
```

## Memory Information

Get comprehensive memory status:

```python
# Check if distributed memory is active
is_distributed = memory_manager.is_distributed()

# Get full memory configuration
info = memory_manager.get_full_memory_info()
print(info)
# Output:
# {
#     "short_term": {...},  # Short-term memory stats
#     "distributed": {
#         "mode": "distributed",
#         "is_distributed": True,
#         "store_enabled": True,
#         "checkpointer_enabled": True,
#         "n_nodes": 4,
#         "managers_per_node": 2,
#         "store_memory_gb": 10.0,
#         "checkpoint_memory_gb": 20.0
#     }
# }
```

## Best Practices

1. **Memory Sizing**: Allocate sufficient memory based on your workload
   - Store: ~1-10 GB for long-term memory
   - Checkpoints: ~2-20 GB for graph state

2. **Node Configuration**: Match `n_nodes` to your cluster allocation

3. **Fallback Strategy**: Keep `fallback_to_local=True` for development

4. **Resource Cleanup**: Always close memory manager to free resources
   ```python
   memory_manager.close()
   ```

5. **Auto Mode**: Use for portable code that works in both dev and HPC environments

## Performance Considerations

- **Distributed Mode**: Best for multi-node HPC workloads with large state
- **Local Mode**: Sufficient for single-node or development environments
- **Memory Overhead**: Dragon DDict adds ~10-20% overhead vs in-memory

## Troubleshooting

### Dragon Not Available

If you see: `⚠ Dragon not available - using local fallback`

- Check Dragon installation: `python -c "import dragon; print(dragon.__version__)"`
- Ensure running in Dragon runtime: `dragon your_script.py`
- Verify HPC environment variables are set

### Memory Allocation Errors

If Dragon fails to allocate memory:

- Reduce `store_total_mem` and `checkpoint_total_mem`
- Check available memory: `free -h`
- Adjust `managers_per_node` (fewer managers = less overhead)

### Import Errors

If imports fail:

- Ensure Flowgentic is installed: `pip install -e .`
- Check Python path includes Flowgentic source

## References

- [Dragon Documentation](https://github.com/DragonHPC/dragon)
- [LangGraph Memory](https://langchain-ai.github.io/langgraph/concepts/#memory)
- [Flowgentic Memory Guide](../../../docs/features/memory.md)
