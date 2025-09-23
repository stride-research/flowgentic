# FlowGentic LangGraph Integration Examples

This directory contains comprehensive examples demonstrating FlowGentic's memory management capabilities integrated with LangGraph workflows.

## Overview

FlowGentic provides advanced memory management for LangGraph agents, enabling:

- **Multiple Memory Strategies**: trim_last, trim_middle, importance_based, summarize
- **Smart Context Retrieval**: Query-based memory search with relevance scoring
- **LLM-Powered Summarization**: Automatic memory compaction using AI
- **AsyncFlow Tool Integration**: Memory-enabled tool execution
- **Memory Health Monitoring**: Statistics and performance metrics

## Examples

### 01-parallel-tools.py
**Basic AsyncFlow Tool Integration**
- Demonstrates parallel tool execution using AsyncFlow backend
- Shows LangGraph integration with concurrent task processing
- Features: Tool parallelism, error handling, result aggregation

### 02-parallel-agents.py
**Multi-Agent Coordination**
- Shows coordination between multiple agents
- Demonstrates agent communication patterns
- Features: Agent orchestration, message passing, workflow management

### 03-memory-basic.py
**Memory Management Fundamentals**
- Core memory strategies: trim_last, trim_middle, importance_based
- Memory statistics and monitoring
- Smart context retrieval with queries
- Features: Memory configuration, strategy comparison, health monitoring

### 04-memory-summarization.py
**LLM-Powered Memory Summarization**
- Automatic memory compaction using AI summarization
- Summarization vs trimming comparison
- Fallback handling for summarization failures
- Features: LLM integration, summarization configuration, fallback strategies

### 05-memory-with-tools.py
**Memory-Enabled Tool Workflows**
- Tools with access to conversation memory
- Context-aware tool responses
- Memory persistence across tool executions
- Features: Memory-enhanced tools, AsyncFlow integration, performance monitoring

## Chatbot Tutorial Series

### chatbot-tutorial/02-chatbot-tools.py
**Basic Tool-Enabled Chatbot**
- Simple chatbot with tool calling capabilities
- Demonstrates LangGraph tool integration

### chatbot-tutorial/03-chatbot-memory.py
**Memory-Enabled Chatbot**
- Chatbot with basic LangGraph memory (checkpointer-based)
- Shows conversation persistence

### chatbot-tutorial/04-chatbot-human-in-the-loop.py
**Advanced Chatbot Features**
- Human-in-the-loop interactions
- Interrupt handling and user approval workflows

## Memory Strategies Explained

### 1. trim_last
- **Behavior**: Keeps the most recent messages, removes oldest
- **Use Case**: Simple chronological memory management
- **Pros**: Fast, predictable
- **Cons**: May lose important early context

### 2. trim_middle
- **Behavior**: Removes middle messages, keeps beginning and end
- **Use Case**: Preserve conversation start and recent context
- **Pros**: Maintains conversation flow
- **Cons**: May lose important middle information

### 3. importance_based
- **Behavior**: Keeps messages based on calculated importance scores
- **Use Case**: Intelligent memory management
- **Pros**: Preserves most valuable information
- **Cons**: Computationally more expensive

### 4. summarize
- **Behavior**: Uses LLM to create summaries of old conversations
- **Use Case**: Long conversations requiring information preservation
- **Pros**: Maximum information retention with minimal tokens
- **Cons**: Requires LLM API calls, may have latency

## Configuration Options

```python
from flowgentic.langGraph.memory import MemoryConfig, MemoryManager

# Basic configuration
config = MemoryConfig(
    max_short_term_messages=50,        # Maximum messages to keep
    short_term_strategy="importance_based",  # Memory strategy
    context_window_buffer=10,          # Buffer for context window
    memory_update_threshold=5         # Updates between memory operations
)

# With summarization
config_with_summary = MemoryConfig(
    max_short_term_messages=50,
    short_term_strategy="summarize",
    enable_summarization=True,
    summarization_batch_size=10
)

memory_manager = MemoryManager(config)
```

## Integration with LangGraph

```python
from flowgentic.langGraph import (
    MemoryEnabledLangGraphIntegration,
    create_memory_enabled_graph
)

# Create memory-enabled integration
integration = MemoryEnabledLangGraphIntegration(backend, memory_manager)

# Create workflow with memory
app = create_memory_enabled_graph(integration, memory_manager, tools)

# Use with user context
config = {"configurable": {"thread_id": "session_1", "user_id": "user123"}}
result = app.invoke({"messages": [user_message], "user_id": "user123"}, config)
```

## Memory Health Monitoring

```python
# Get memory statistics
stats = memory_manager.get_memory_health()
print(f"Total messages: {stats['total_messages']}")
print(f"Memory efficiency: {stats['memory_efficiency']:.1%}")
print(f"Average importance: {stats['average_importance']:.2f}")

# Consolidate memory
consolidation_result = await memory_manager.consolidate_memory()
```

## Smart Context Retrieval

```python
# Query-based context retrieval
context = await memory_manager.get_relevant_context(user_id, query="pizza")

# Access different context types
short_term = context["short_term"]          # Recent messages
relevant = context["relevant_messages"]    # Query-relevant messages
stats = context["memory_stats"]            # Memory statistics
```

## Running Examples

```bash
# Basic memory strategies
python 03-memory-basic.py

# Detailed memory analysis
python 03-memory-basic.py --detailed

# Memory summarization
python 04-memory-summarization.py

# Compare with/without summarization
python 04-memory-summarization.py --compare

# Summarization fallback testing
python 04-memory-summarization.py --fallback

# Memory with tools
python 05-memory-with-tools.py

# Memory consolidation demo
python 05-memory-with-tools.py --consolidation
```

## Key Benefits

1. **Intelligent Memory Management**: Automatically manages conversation length while preserving important information
2. **Flexible Strategies**: Choose the right memory approach for your use case
3. **LLM Integration**: Use AI to summarize and compact memories intelligently
4. **Tool Integration**: Tools can access conversation context for better responses
5. **Performance Monitoring**: Track memory efficiency and system performance
6. **AsyncFlow Compatibility**: Seamless integration with existing AsyncFlow workflows

## Architecture

```
MemoryManager
├── ShortTermMemoryManager (conversation memory)
│   ├── MemoryConfig (strategy settings)
│   ├── Memory trimming strategies
│   └── Importance scoring
├── Memory consolidation
├── Smart context retrieval
└── Health monitoring

MemoryEnabledLangGraphIntegration
├── LangGraph workflow integration
├── Memory-aware tool execution
└── Context-enhanced responses
```

## Best Practices

1. **Choose Appropriate Strategy**: importance_based for most use cases, summarize for long conversations
2. **Monitor Memory Health**: Regularly check memory efficiency and adjust limits as needed
3. **Configure Summarization**: Use summarization_batch_size to balance API costs and information preservation
4. **Handle Fallbacks**: Always have fallback strategies when LLM services are unavailable
5. **Context Window Awareness**: Consider LLM context limits when setting max_short_term_messages

## Troubleshooting

- **Memory not trimming**: Check max_short_term_messages setting
- **Summarization failing**: Verify LLM configuration and API keys
- **Poor context retrieval**: Adjust importance scoring or try different strategies
- **Performance issues**: Monitor memory efficiency and consider consolidation

For more information, see the main FlowGentic documentation and LangGraph memory guides.</content>
</xai:function_call:>Write to file: examples/langgraph-integration/README.md