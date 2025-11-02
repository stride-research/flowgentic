# Memory Management

FlowGentic provides a sophisticated memory management system for LangGraph agents that enables stateful conversation handling, intelligent context retrieval, and efficient memory optimization. The system is designed to help agents maintain conversation history while managing token limits and preserving important context.

## Overview

The memory system consists of three main components:

1. **Short-Term Memory**: Thread-scoped conversation history with configurable trimming strategies
2. **Memory Configuration**: Flexible configuration for memory strategies and limits
3. **Memory Manager**: High-level interface for memory operations and context retrieval

## Key Features

- **Multiple Trimming Strategies**: Choose from `trim_last`, `trim_middle`, `importance_based`, or `summarize`
- **LLM-Powered Summarization**: Automatically summarize old conversations to preserve context while reducing token usage
- **Importance Scoring**: Automatically calculate and track message importance based on type, content, and recency
- **Smart Context Retrieval**: Semantic search and relevance scoring for retrieving pertinent conversation history
- **Memory Health Monitoring**: Track memory usage, efficiency, and statistics
- **Automatic Consolidation**: Periodic memory optimization and cleanup
- **Fallback Handling**: Graceful degradation when summarization fails

## Quick Start

### Basic Memory Setup

```python
from flowgentic.langGraph.memory import MemoryManager, MemoryConfig
from langchain_core.messages import HumanMessage, AIMessage

# Configure memory
memory_config = MemoryConfig(
    max_short_term_messages=50,
    short_term_strategy="importance_based",
    context_window_buffer=10
)

# Create memory manager
memory_manager = MemoryManager(memory_config)

# Add interactions
await memory_manager.add_interaction(
    user_id="user_123",
    messages=[
        HumanMessage(content="Hello!"),
        AIMessage(content="Hi! How can I help you today?")
    ]
)

# Get relevant context
context = await memory_manager.get_relevant_context(
    user_id="user_123",
    query="greeting"
)
```

## Memory Configuration

The `MemoryConfig` class provides fine-grained control over memory behavior:

```python
memory_config = MemoryConfig(
    max_short_term_messages=50,           # Maximum messages to keep in memory
    short_term_strategy="importance_based", # Trimming strategy
    context_window_buffer=10,             # Buffer messages in context window
    memory_update_threshold=5,            # Update memory every N interactions
    summarization_batch_size=10,          # Messages to summarize at once
    enable_summarization=False            # Enable LLM-based summarization
)
```

### Configuration Parameters

| Parameter                  | Type   | Default       | Description                                               |
| -------------------------- | ------ | ------------- | --------------------------------------------------------- |
| `max_short_term_messages`  | `int`  | `50`          | Maximum number of messages to retain in short-term memory |
| `short_term_strategy`      | `str`  | `"trim_last"` | Strategy for trimming messages when limit is reached      |
| `context_window_buffer`    | `int`  | `10`          | Number of buffer messages to keep in context window       |
| `memory_update_threshold`  | `int`  | `5`           | Frequency of memory updates (every N interactions)        |
| `summarization_batch_size` | `int`  | `10`          | Number of messages to summarize in a single batch         |
| `enable_summarization`     | `bool` | `False`       | Enable LLM-powered summarization                          |

## Trimming Strategies

### 1. Trim Last (`trim_last`)

Keeps the most recent messages, removing oldest messages first. System messages are always preserved.

**Best for**: Simple conversations where recent context is most important.

```python
memory_config = MemoryConfig(
    max_short_term_messages=10,
    short_term_strategy="trim_last"
)
```

**Example**:

```
Before: [Sys, Msg1, Msg2, Msg3, Msg4, Msg5, Msg6]
After:  [Sys, Msg4, Msg5, Msg6]  # Keeps last 3 + system
```

### 2. Trim Middle (`trim_middle`)

Removes messages from the middle of the conversation, preserving both the beginning and end context.

**Best for**: Conversations where both initial context and recent exchanges are important.

```python
memory_config = MemoryConfig(
    max_short_term_messages=10,
    short_term_strategy="trim_middle"
)
```

**Example**:

```
Before: [Sys, Msg1, Msg2, Msg3, Msg4, Msg5, Msg6]
After:  [Sys, Msg1, Msg2, Msg5, Msg6]  # Removes middle messages
```

### 3. Importance Based (`importance_based`)

Keeps messages with the highest importance scores. Importance is calculated based on:

- **Message Type**: System messages (2.0), AI messages with tool calls (1.5), Human messages (1.2), Others (1.0)
- **Content Length**: Longer messages get a bonus (up to +1.0)
- **Recency**: More recent messages receive higher scores

**Best for**: Complex conversations where content quality matters more than recency.

```python
memory_config = MemoryConfig(
    max_short_term_messages=10,
    short_term_strategy="importance_based"
)
```

**Example**:

```
Before: [Sys(2.0), Msg1(1.2), Msg2(1.8), Msg3(1.1), Msg4(1.5)]
After:  [Sys(2.0), Msg2(1.8), Msg4(1.5), Msg1(1.2)]  # Keeps highest scores
```

### 4. Summarize (`summarize`)

Uses an LLM to create summaries of old messages, preserving information while reducing token count.

**Best for**: Long conversations where context must be preserved but token limits are a concern.

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(model="gpt-4")

memory_config = MemoryConfig(
    max_short_term_messages=20,
    short_term_strategy="summarize",
    enable_summarization=True,
    summarization_batch_size=10
)

memory_manager = MemoryManager(memory_config, llm=llm)
```

**Features**:

- Automatically summarizes old messages when memory limit is reached
- Keeps recent messages unsummarized for immediate context
- Falls back to `trim_last` if summarization fails
- Configurable batch size for summarization

## Memory Operations

### Adding Interactions

```python
# Add a single interaction
result = await memory_manager.add_interaction(
    user_id="user_123",
    messages=[
        HumanMessage(content="What's the weather?"),
        AIMessage(content="It's sunny today!")
    ],
    metadata={"session_id": "abc123"}  # Optional metadata
)

print(f"Short-term messages: {result['short_term_messages']}")
print(f"Total interactions: {result['total_interactions']}")
```

### Retrieving Context

The memory manager provides intelligent context retrieval with semantic search:

```python
# Get relevant context for a query
context = await memory_manager.get_relevant_context(
    user_id="user_123",
    query="weather information"
)

# Access different parts of the context
short_term = context["short_term"]           # All short-term messages
relevant = context["relevant_messages"]      # Most relevant messages
stats = context["memory_stats"]              # Memory statistics
```

**Relevance Scoring**: The system uses multiple techniques to find relevant messages:

- Exact phrase matching (highest score)
- Word overlap (Jaccard similarity)
- Consecutive word matches (n-grams)
- Importance and recency boosting

### Memory Statistics

```python
# Get current memory state
stats = memory_manager.get_memory_health()

print(f"Total messages: {stats['total_messages']}")
print(f"Memory efficiency: {stats['memory_efficiency']:.1%}")
print(f"Average importance: {stats['average_importance']:.2f}")
print(f"Interaction count: {stats['interaction_count']}")
```

### Memory Consolidation

Periodically consolidate memory to optimize storage and update importance scores:

```python
# Consolidate memory
result = await memory_manager.consolidate_memory()

print(f"Consolidated messages: {result['short_term']['consolidated_messages']}")
print(f"Average importance: {result['short_term']['avg_importance']:.2f}")
```

**What consolidation does**:

1. Synchronizes message history with memory items
2. Updates importance scores based on recency
3. Removes low-importance items if memory is full
4. Applies time-based decay to importance scores

### Clearing Memory

```python
# Clear short-term memory
memory_manager.clear_short_term_memory()

# Or clear specific short-term manager
short_term_manager = memory_manager.short_term_manager
short_term_manager.clear()
```

## Advanced Usage

### Custom Importance Calculation

The system automatically calculates importance, but you can understand the scoring:

```python
from flowgentic.langGraph.memory import ShortTermMemoryItem

# View importance scores
for item in memory_manager.short_term_manager.memory_items:
    print(f"{item.message_type}: {item.importance:.2f} - {item.content[:50]}...")
```

### Direct Short-Term Memory Access

For fine-grained control, access the short-term manager directly:

```python
from flowgentic.langGraph.memory import ShortTermMemoryManager

short_term = ShortTermMemoryManager(memory_config, llm)

# Add messages directly
messages = [HumanMessage(content="Hello")]
result = short_term.add_messages(messages)

# Get recent messages
recent = short_term.get_recent_messages(count=5)

# Get statistics
stats = short_term.get_memory_stats()
```

### Memory-Enabled LangGraph Integration

FlowGentic provides seamless integration with LangGraph:

```python
from flowgentic.langGraph import (
    MemoryEnabledLangGraphIntegration,
    create_memory_enabled_graph
)
from langgraph.graph import StateGraph

# Create memory-enabled integration
integration = MemoryEnabledLangGraphIntegration(
    backend=asyncflow_backend,
    memory_manager=memory_manager
)

# Build graph with memory support
graph = create_memory_enabled_graph(
    state_schema=MemoryEnabledState,
    memory_manager=memory_manager
)
```

## Examples

### Example 1: Basic Memory with Trimming Strategies

See the complete example: [03-memory-basic.py](../../examples/langgraph-integration/03-memory-basic.py)

```python
from flowgentic.langGraph.memory import MemoryManager, MemoryConfig
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

# Configure with trim_last strategy
memory_config = MemoryConfig(
    max_short_term_messages=5,
    short_term_strategy="trim_last"
)

manager = ShortTermMemoryManager(memory_config)

# Add messages
messages = [
    SystemMessage(content="You are a helpful assistant"),
    HumanMessage(content="Hello, how are you?"),
    AIMessage(content="I'm doing well, thank you!"),
    HumanMessage(content="Can you help me?"),
    AIMessage(content="Of course! I'd be happy to help."),
    HumanMessage(content="Tell me about memory management"),
    AIMessage(content="Memory management is crucial..."),
]

final_messages = manager.add_messages(messages)
print(f"Kept {len(final_messages)} messages")
```

### Example 2: LLM-Powered Summarization

See the complete example: [04-memory-summarization.py](../../examples/langgraph-integration/04-memory-summarization.py)

```python
from langchain_openai import ChatOpenAI

# Setup LLM for summarization
llm = ChatOpenAI(model="gpt-4")

memory_config = MemoryConfig(
    max_short_term_messages=10,
    short_term_strategy="summarize",
    enable_summarization=True,
    summarization_batch_size=5
)

memory_manager = MemoryManager(memory_config, llm)

# Add many messages - old ones will be summarized
for i in range(20):
    await memory_manager.add_interaction(
        "user_123",
        [
            HumanMessage(content=f"Question {i}"),
            AIMessage(content=f"Answer {i}")
        ]
    )

# Memory now contains summaries + recent messages
messages = memory_manager.get_short_term_messages()
print(f"Total messages: {len(messages)}")  # Will be <= 10
```

### Example 3: Memory with Tool Integration

See the complete example: [05-memory-with-tools.py](../../examples/langgraph-integration/05-memory-with-tools.py)

```python
from flowgentic.langGraph import MemoryEnabledLangGraphIntegration
from langchain_core.tools import tool

# Create memory-enabled workflow
integration = MemoryEnabledLangGraphIntegration(
    backend=asyncflow_backend,
    memory_manager=memory_manager
)

# Register tools with memory awareness
@integration.asyncflow_tool
async def weather_tool(city: str) -> dict:
    """Get weather with memory context."""
    # Tool has access to memory context
    context = await memory_manager.get_relevant_context("user", city)
    return {"city": city, "temp": 72}

# Tools automatically benefit from memory
result = await integration.execute_tool("weather_tool", {"city": "NYC"})
```

### Example 4: Interactive Memory-Enabled Chatbot

```python
async def chatbot_with_memory():
    memory_config = MemoryConfig(
        max_short_term_messages=10,
        short_term_strategy="importance_based"
    )

    memory_manager = MemoryManager(memory_config)

    while True:
        user_input = input("You: ")
        if user_input.lower() == 'quit':
            break

        # Get relevant context
        context = await memory_manager.get_relevant_context(
            "user",
            user_input
        )

        # Use context with your LLM
        response = await llm.ainvoke(context["short_term"] + [
            HumanMessage(content=user_input)
        ])

        # Add to memory
        await memory_manager.add_interaction("user", [
            HumanMessage(content=user_input),
            response
        ])

        print(f"Assistant: {response.content}")
```

## Best Practices

### 1. Choose the Right Strategy

- **Short conversations**: Use `trim_last` for simplicity
- **Long conversations with context**: Use `summarize` with an LLM
- **Quality over recency**: Use `importance_based`
- **Balanced approach**: Use `trim_middle`

### 2. Configure Appropriate Limits

```python
# For chatbots with token limits
memory_config = MemoryConfig(
    max_short_term_messages=30,  # Adjust based on your model's context window
    context_window_buffer=5
)

# For long-running agents
memory_config = MemoryConfig(
    max_short_term_messages=100,
    short_term_strategy="importance_based",
    memory_update_threshold=10
)
```

### 3. Monitor Memory Health

```python
# Periodically check memory health
if interaction_count % 10 == 0:
    health = memory_manager.get_memory_health()
    if health['memory_efficiency'] > 0.9:
        # Memory is getting full, consider consolidation
        await memory_manager.consolidate_memory()
```

### 4. Use Summarization for Long Conversations

```python
# Enable summarization for conversations that may exceed token limits
memory_config = MemoryConfig(
    max_short_term_messages=50,
    short_term_strategy="summarize",
    enable_summarization=True,
    summarization_batch_size=15  # Summarize in larger batches
)
```

### 5. Leverage Context Retrieval

```python
# Always use relevant context retrieval for better responses
context = await memory_manager.get_relevant_context(
    user_id="user_123",
    query=user_input  # Pass the current query for semantic search
)

# Use only relevant messages to save tokens
relevant_messages = context["relevant_messages"]
```

## Performance Considerations

### Memory Overhead

- Each message creates a `ShortTermMemoryItem` with metadata
- Importance scores are recalculated during consolidation
- Summarization requires LLM API calls (costs and latency)

### Optimization Tips

1. **Batch Operations**: Add multiple messages at once instead of one by one
2. **Consolidation Frequency**: Balance between memory efficiency and computational cost
3. **Summarization Batch Size**: Larger batches are more efficient but less granular
4. **Strategy Selection**: `trim_last` is fastest, `importance_based` requires more computation

### Benchmarks

Typical performance characteristics:

| Operation         | Time Complexity | Notes                          |
| ----------------- | --------------- | ------------------------------ |
| Add messages      | O(n)            | n = number of messages         |
| Trim last         | O(n)            | Linear scan                    |
| Trim middle       | O(n)            | Linear scan                    |
| Importance-based  | O(n log n)      | Sorting required               |
| Summarize         | O(n) + LLM      | Includes LLM API call          |
| Context retrieval | O(n\*m)         | n = messages, m = query length |
| Consolidation     | O(n)            | Updates all items              |

## API Reference

For detailed API documentation, see: [Memory API Reference](../../api/flowgentic/langGraph/memory/)

### Core Classes

- **`MemoryConfig`**: Configuration for memory management
- **`MemoryManager`**: High-level memory management interface
- **`ShortTermMemoryManager`**: Short-term memory implementation
- **`ShortTermMemoryItem`**: Individual memory item with metadata
- **`MemoryEnabledState`**: State schema for memory-enabled graphs

### Key Methods

- `add_interaction()`: Add messages to memory
- `get_relevant_context()`: Retrieve relevant conversation history
- `get_memory_health()`: Get memory statistics and health metrics
- `consolidate_memory()`: Optimize and clean up memory
- `clear_short_term_memory()`: Clear all short-term memory

## Troubleshooting

### Issue: Memory Growing Too Large

**Solution**: Reduce `max_short_term_messages` or enable consolidation:

```python
memory_config = MemoryConfig(
    max_short_term_messages=30,  # Reduce limit
    memory_update_threshold=5    # Consolidate more frequently
)
```

### Issue: Summarization Failing

**Solution**: The system automatically falls back to trimming. Check LLM configuration:

```python
# Ensure LLM is properly configured
llm = ChatOpenAI(model="gpt-4", temperature=0)
memory_manager = MemoryManager(memory_config, llm=llm)
```

### Issue: Important Context Being Lost

**Solution**: Use `importance_based` or `summarize` strategy:

```python
memory_config = MemoryConfig(
    short_term_strategy="importance_based",
    max_short_term_messages=50  # Increase limit
)
```

### Issue: High Memory Usage

**Solution**: Consolidate memory more frequently:

```python
# Consolidate every N interactions
if interaction_count % 5 == 0:
    await memory_manager.consolidate_memory()
```

## Future Enhancements

The memory system is designed for extensibility. Planned features include:

- **Long-Term Memory**: Persistent storage with vector databases
- **Semantic Clustering**: Group related messages automatically
- **Multi-User Memory**: Separate memory spaces per user
- **Memory Compression**: Advanced compression techniques
- **Custom Importance Functions**: User-defined importance scoring

## Related Features

- [Telemetry](telemetry.md): Monitor memory usage and performance
- [Fault Tolerance](fault_tolerance.md): Handle memory operation failures
- [LangGraph Integration](../architecture.md): Build memory-enabled graphs

## Learn More

- [Examples Directory](../../examples/langgraph-integration/)
- [API Reference](../../api/flowgentic/langGraph/memory/)
- [Architecture Overview](../architecture.md)
