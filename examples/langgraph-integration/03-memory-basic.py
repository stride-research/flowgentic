"""
Basic Memory Management Demonstration

This example demonstrates the FlowGentic memory management system with different
trimming strategies. It shows how to:

1. Configure memory settings
2. Use different trimming strategies (trim_last, trim_middle, importance_based)
3. Monitor memory usage and statistics
4. Understand memory consolidation

FEATURES:
    [X] Basic memory management
    [X] Multiple trimming strategies
    [X] Memory statistics and monitoring
    [ ] Advanced summarization (see 04-memory-summarization.py)
    [ ] Tool integration (see 05-memory-with-tools.py)
"""

import asyncio
from typing import List, cast
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage

# Import our memory components
from flowgentic.langGraph.memory import (
    MemoryManager,
    MemoryConfig,
    ShortTermMemoryManager
)





async def demonstrate_memory_strategies():
    """Demonstrate different memory trimming strategies."""

    print("=" * 60)
    print("FlowGentic Memory Management Examples")
    print("=" * 60)

    # Example 1: Basic memory with trim_last strategy
    print("\n1. TRIM_LAST Strategy")
    print("-" * 30)

    memory_config = MemoryConfig(
        max_short_term_messages=5,
        short_term_strategy="trim_last",  # Keep most recent messages
        context_window_buffer=2
    )

    manager = ShortTermMemoryManager(memory_config)

    # Add some messages
    messages = cast(List[BaseMessage], [
        SystemMessage(content="You are a helpful assistant"),
        HumanMessage(content="Hello, how are you?"),
        AIMessage(content="I'm doing well, thank you for asking!"),
        HumanMessage(content="Can you help me with a task?"),
        AIMessage(content="Of course! I'd be happy to help."),
        HumanMessage(content="Tell me about memory management"),  # This will trigger trimming
        AIMessage(content="Memory management is crucial for AI systems..."),
        HumanMessage(content="What are the different strategies?"),
    ])

    print(f"Adding {len(messages)} messages to memory...")
    final_messages = manager.add_messages(messages)

    print(f"Memory now contains {len(final_messages)} messages:")
    for i, msg in enumerate(final_messages):
        msg_type = type(msg).__name__
        content = str(msg.content) if msg.content else ""
        preview = content[:50] + "..." if len(content) > 50 else content
        print(f"  {i+1}. {msg_type}: {preview}")

    # Get memory statistics
    stats = manager.get_memory_stats()
    print(f"\nMemory Statistics:")
    print(f"  Total messages: {stats['total_messages']}")
    print(f"  System messages: {stats['system_messages']}")
    print(f"  Human messages: {stats['human_messages']}")
    print(f"  AI messages: {stats['ai_messages']}")
    print(f"  Interaction count: {stats['interaction_count']}")

    # Example 2: trim_middle strategy
    print("\n\n2. TRIM_MIDDLE Strategy")
    print("-" * 32)

    memory_config = MemoryConfig(
        max_short_term_messages=5,
        short_term_strategy="trim_middle",  # Keep beginning and end, remove middle
        context_window_buffer=2
    )

    manager = ShortTermMemoryManager(memory_config)

    # Add the same messages
    final_messages = manager.add_messages(messages)

    print(f"Memory now contains {len(final_messages)} messages:")
    for i, msg in enumerate(final_messages):
        msg_type = type(msg).__name__
        content = str(msg.content) if msg.content else ""
        preview = content[:50] + "..." if len(content) > 50 else content
        print(f"  {i+1}. {msg_type}: {preview}")

    # Example 3: importance_based strategy
    print("\n\n3. IMPORTANCE_BASED Strategy")
    print("-" * 34)

    memory_config = MemoryConfig(
        max_short_term_messages=5,
        short_term_strategy="importance_based",  # Keep most important messages
        context_window_buffer=2
    )

    manager = ShortTermMemoryManager(memory_config)

    # Add the same messages
    final_messages = manager.add_messages(messages)

    print(f"Memory now contains {len(final_messages)} messages:")
    for i, msg in enumerate(final_messages):
        msg_type = type(msg).__name__
        content = str(msg.content) if msg.content else ""
        preview = content[:50] + "..." if len(content) > 50 else content
        print(f"  {i+1}. {msg_type}: {preview}")

    # Show memory items with importance scores
    print(f"\nMemory items with importance scores:")
    for i, item in enumerate(manager.memory_items[-5:]):  # Show last 5
        print(f"  {i+1}. {item.message_type}: {item.importance:.2f} - {item.content[:40]}...")

    # Example 4: Memory Manager with health monitoring
    print("\n\n4. MEMORY MANAGER with Health Monitoring")
    print("-" * 42)

    memory_config = MemoryConfig(
        max_short_term_messages=10,
        short_term_strategy="importance_based",
        context_window_buffer=2,
        memory_update_threshold=3
    )

    memory_manager = MemoryManager(memory_config)

    # Simulate multiple interactions
    for i in range(3):
        interaction_messages = cast(List[BaseMessage], [
            HumanMessage(content=f"Interaction {i+1}: Question {j+1}")
            for j in range(3)
        ] + [
            AIMessage(content=f"Interaction {i+1}: Response {j+1}")
            for j in range(3)
        ])
        await memory_manager.add_interaction(f"user_{i+1}", interaction_messages)

    # Get memory health
    health = memory_manager.get_memory_health()
    print("Memory Health Report:")
    print(f"  Configuration: max_messages={health['config']['max_short_term_messages']}, strategy={health['config']['short_term_strategy']}")
    print(f"  Memory Efficiency: {health['memory_efficiency']:.1%}")
    print(f"  Average Importance: {health['average_importance']:.2f}")
    print(f"  Total Interactions: {health['interaction_count']}")

    # Example 5: Context retrieval
    print("\n\n5. SMART CONTEXT RETRIEVAL")
    print("-" * 30)

    # Get relevant context for a user
    context = await memory_manager.get_relevant_context("user_1", query="Question")

    print("Retrieved Context:")
    print(f"  Short-term messages: {len(context['short_term'])}")
    print(f"  Relevant messages: {len(context['relevant_messages'])}")
    print(f"  Memory stats: {context['memory_stats']}")

    print("\nRelevant messages:")
    for i, msg in enumerate(context['relevant_messages'][:3]):  # Show first 3
        msg_type = type(msg).__name__
        preview = msg.content[:50] + "..." if len(msg.content) > 50 else msg.content
        print(f"  {i+1}. {msg_type}: {preview}")




async def interactive_memory_demo():
    """Interactive demonstration of memory-enabled conversation."""

    print("\n" + "=" * 60)
    print("Interactive Memory-Enabled Chatbot")
    print("=" * 60)
    print("Type 'quit' to exit, 'stats' to see memory statistics")
    print("-" * 60)

    # Setup memory-enabled chatbot
    memory_config = MemoryConfig(
        max_short_term_messages=10,
        short_term_strategy="importance_based",
        context_window_buffer=3
    )

    memory_manager = MemoryManager(memory_config)

    conversation_history = []

    while True:
        user_input = input("\nYou: ").strip()

        if user_input.lower() in ['quit', 'exit', 'q']:
            print("Goodbye!")
            break

        if user_input.lower() == 'stats':
            stats = memory_manager.get_memory_health()
            print("\nMemory Statistics:")
            print(f"- Total messages: {stats['total_messages']}")
            print(f"- Memory efficiency: {stats['memory_efficiency']:.1%}")
            print(f"- Average importance: {stats['average_importance']:.2f}")
            print(f"- Interaction count: {stats['interaction_count']}")
            continue

        # Add user message
        user_message = HumanMessage(content=user_input)
        conversation_history.append(user_message)

        # Get memory context
        context = await memory_manager.get_relevant_context("demo_user", user_input)

        # Simulate AI response (in real usage, this would come from your LLM)
        ai_response = AIMessage(content=f"I received your message: '{user_input}' (with {len(context.get('short_term', []))} messages in context)")

        conversation_history.append(ai_response)

        # Add to memory
        await memory_manager.add_interaction("demo_user", [user_message, ai_response])

        print(f"Assistant: {ai_response.content}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--interactive":
        # Run interactive demo
        asyncio.run(interactive_memory_demo())
    else:
        # Run strategy demonstration
        asyncio.run(demonstrate_memory_strategies())
