# Memory-Enabled Sequential Workflow Example

A comprehensive example demonstrating how to integrate FlowGentic's advanced memory management system with a sequential (pipeline) workflow pattern.

## Overview

This example extends the base sequential workflow with intelligent memory capabilities, allowing the workflow to:
- Maintain context across multiple stages
- Retrieve relevant information from conversation history
- Prioritize important messages using importance scoring
- Track memory health and efficiency
- Provide memory-aware agent responses

## Key Features

### 🧠 Memory Management
- **Importance-Based Trimming**: Automatically preserves most valuable messages
- **Context Retrieval**: Each stage accesses relevant memory for better decisions
- **Memory Statistics**: Track memory usage, efficiency, and health metrics
- **Configurable Strategies**: Choose from trim_last, trim_middle, importance_based, or summarize

### 🔄 Sequential Stages with Memory
1. **Preprocessing** - Validates input and initializes memory
2. **Research Agent** - Conducts research using memory context
3. **Context Preparation** - Prepares synthesis context from memory
4. **Synthesis Agent** - Creates deliverables with full memory awareness
5. **Finalization** - Formats output with memory statistics

### 📊 Telemetry & Monitoring
- Memory operations tracking
- Memory health metrics
- Stage-by-stage memory updates
- Full introspection reports

## Architecture

```
┌─────────────────┐
│  User Input     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│  Preprocessing  │────▶│    Memory    │
└────────┬────────┘     │   Manager    │
         │              └──────┬───────┘
         ▼                     │
┌─────────────────┐           │
│ Research Agent  │◀──────────┤
│  (with memory)  │           │
└────────┬────────┘           │
         │                     │
         ▼                     │
┌─────────────────┐           │
│    Context      │◀──────────┤
│  Preparation    │           │
└────────┬────────┘           │
         │                     │
         ▼                     │
┌─────────────────┐           │
│ Synthesis Agent │◀──────────┤
│  (with memory)  │           │
└────────┬────────┘           │
         │                     │
         ▼                     │
┌─────────────────┐           │
│  Finalization   │◀──────────┘
│  (with stats)   │
└─────────────────┘
```

## File Structure

```
research_agent_with_memory/
├── main.py                          # Entry point with memory initialization
├── components/
│   ├── builder.py                   # Graph builder with memory integration
│   ├── nodes.py                     # Memory-aware workflow nodes
│   ├── edges.py                     # Conditional routing logic
│   └── utils/
│       ├── actions.py               # Memory-aware tools and tasks
│       └── actions_registry.py      # Centralized tool registry
├── utils/
│   └── schemas.py                   # State schemas with memory fields
└── README.md                        # This file
```

## How to Run

### Prerequisites
```bash
# Ensure FlowGentic is installed
pip install '.'

# Set your API key
export OPEN_ROUTER_API_KEY=your-key-here
```

### Run the Example
```bash
# From the repository root
python -m examples.langgraph-integration.design_patterns.sequential.research_agent_with_memory.main
```

## Memory Configuration

The example uses importance-based memory strategy:

```python
memory_config = MemoryConfig(
    max_short_term_messages=50,           # Maximum messages to retain
    short_term_strategy="importance_based", # Strategy: trim_last, trim_middle, importance_based, summarize
    context_window_buffer=10,             # Buffer for context window
    memory_update_threshold=5,            # Update frequency
    enable_summarization=False,           # Enable LLM-based summarization
)
```

### Available Memory Strategies

| Strategy | Behavior | Best For |
|----------|----------|----------|
| `trim_last` | Keep most recent messages | Simple chronological workflows |
| `trim_middle` | Keep beginning and end | Preserving initial context |
| `importance_based` | Keep most important messages | Complex multi-stage workflows |
| `summarize` | LLM-powered summarization | Long conversations |

## Memory-Aware Node Pattern

Each node follows this pattern for memory integration:

```python
@agents_manager.execution_wrappers.asyncflow(flow_type=AsyncFlowType.EXECUTION_BLOCK)
async def memory_aware_node(state: WorkflowState) -> WorkflowState:
    # 1. Retrieve relevant context from memory
    memory_context = await memory_manager.get_relevant_context(
        user_id=state.user_id,
        query="relevant information for this stage"
    )
    
    # 2. Use context in agent reasoning
    relevant_messages = memory_context.get("relevant_messages", [])
    system_message = SystemMessage(
        content=f"Previous context: {relevant_messages}"
    )
    
    # 3. Execute agent with memory context
    result = await agent.ainvoke({"messages": [system_message, ...]})
    
    # 4. Update memory with new interactions
    await memory_manager.add_interaction(
        user_id=state.user_id,
        messages=result["messages"]
    )
    
    # 5. Track memory operations
    state.memory_operations.append("node_memory_update")
    
    return state
```

## Expected Output

```
================================================================================
🧠 MEMORY-ENABLED SEQUENTIAL WORKFLOW
================================================================================

🔧 Initializing Memory Manager...
   Strategy: importance_based
   Max messages: 50
   Summarization: Disabled

🏗️ Building Workflow...
🔧 Registering tools and tasks...
📊 Creating state graph...
🔗 Adding nodes with introspection...
🔀 Defining conditional edges...
✅ Workflow graph built successfully with memory integration!

🚀 Starting Memory-Enabled Sequential Workflow
================================================================================
📝 User Input: I need to research the latest developments in renewable energy...

🔄 Preprocessing Node (Memory-Enabled): Starting input validation...
✅ Preprocessing complete: 34 words, domain: energy
📊 Memory: 1 messages stored

🔍 Research Agent Node (Memory-Enabled): Starting research...
🧠 Retrieved 1 relevant messages from memory
✅ Research complete (took 2.34s)
📊 Memory now contains 5 messages

🔧 Context Preparation Node (Memory-Enabled): Preparing synthesis context...
🧠 Context prepared with 5 memory messages
📊 Memory efficiency: 10.0%
✅ Context preparation complete

🏗️ Synthesis Agent Node (Memory-Enabled): Creating deliverables...
🧠 Synthesizing with 5 relevant memory items
✅ Synthesis complete (took 3.12s)

📄 Finalize Output Node (Memory-Enabled): Formatting final results...
✅ Final output formatting complete

🧠 Final Memory Statistics:
   - Total messages: 15
   - Memory efficiency: 30.0%
   - Average importance: 1.42
   - Operations performed: 5

================================================================================
📊 Generating Introspection Report...
📈 Rendering Graph Visualization...

================================================================================
🧠 FINAL MEMORY STATISTICS
================================================================================
   Total messages: 15
   Memory efficiency: 30.0%
   Average importance: 1.42
   System messages: 3
   Human messages: 4
   AI messages: 8

✅ Workflow completed successfully!
================================================================================
```

## Memory Statistics Explained

- **Total messages**: Number of messages currently in memory
- **Memory efficiency**: Percentage of max capacity used
- **Average importance**: Mean importance score (higher = more valuable)
- **Message breakdown**: Count by type (System, Human, AI)

## Customization

### Change Memory Strategy

Edit `main.py`:
```python
memory_config = MemoryConfig(
    max_short_term_messages=100,  # Increase capacity
    short_term_strategy="summarize",  # Use LLM summarization
    enable_summarization=True,  # Enable summarization
)
```

### Add Memory-Aware Tools

In `components/utils/actions.py`:
```python
@agents_manager.execution_wrappers.asyncflow(flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION)
async def memory_aware_tool(query: str, memory_context: Dict[str, Any]) -> str:
    """Tool that leverages memory context."""
    relevant_info = memory_context.get("relevant_messages", [])
    # Use relevant_info to enhance tool response
    return enhanced_result
```

### Monitor Memory Health

```python
# Get memory health metrics
health = memory_manager.get_memory_health()
print(f"Memory efficiency: {health['memory_efficiency']:.1%}")

# Consolidate memory
consolidation_result = await memory_manager.consolidate_memory()
```

## Troubleshooting

### Memory not trimming
- Check `max_short_term_messages` setting
- Verify memory strategy is set correctly
- Ensure `add_interaction()` is being called

### Context retrieval returning empty
- Verify `user_id` is consistent across calls
- Check that messages are being added to memory
- Try different query strings for relevance matching

### Memory efficiency too high
- Reduce `max_short_term_messages`
- Enable summarization for long conversations
- Use `consolidate_memory()` periodically

### Performance issues
- Monitor `memory_efficiency` metric
- Consider using `trim_last` for faster performance
- Reduce `max_short_term_messages` if memory overhead is high

## Performance Considerations

- **Memory Strategy Impact**:
  - `trim_last`: Fastest (O(1))
  - `trim_middle`: Fast (O(n))
  - `importance_based`: Moderate (O(n log n))
  - `summarize`: Slowest (requires LLM call)

- **Memory Overhead**: ~100-200 bytes per message
- **Recommended Max Messages**: 50-100 for most workflows
- **Summarization Frequency**: Every 20-30 messages for long conversations

## Comparison with Base Sequential Pattern

| Feature | Base Sequential | Memory-Enabled Sequential |
|---------|----------------|---------------------------|
| Context Awareness | ❌ Limited | ✅ Full memory access |
| Multi-turn Coherence | ⚠️ Basic | ✅ Excellent |
| Information Retention | ❌ None | ✅ Importance-based |
| Memory Overhead | ✅ Minimal | ⚠️ Moderate |
| Complexity | ✅ Simple | ⚠️ Moderate |
| Best For | Single-pass workflows | Multi-stage research/analysis |

## Next Steps

- Explore [Supervisor with Memory](../../supervisor/supervisor_with_memory/) for multi-agent memory
- Try [Parallel Agents with Memory](../../parallel/parallel_agents_with_memory/) for concurrent memory access
- Review [Memory API Documentation](../../../../../docs/features/memory.md) for advanced features

## Related Examples

- [Basic Sequential Pattern](../research_agent/) - Base pattern without memory
- [Memory Basics](../../../03-memory-basic.py) - Standalone memory examples
- [Memory with Summarization](../../../04-memory-summarization.py) - LLM-powered memory

## Contributing

Found an issue or have an improvement? See [CONTRIBUTING.md](../../../../../CONTRIBUTING.md) for guidelines.

## License

MIT License - see [LICENSE](../../../../../LICENSE) for details.
