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

    # Initialize mock LLM
    llm = MockLLM()

    # Example 1: Basic memory with trim_last strategy
    print("\n1. TRIM_LAST Strategy")
    print("-" * 30)

    memory_config = MemoryConfig(
        max_short_term_messages=5,
        short_term_strategy="trim_last",  # Keep most recent messages
        context_window_buffer=2
    )

    memory_manager = MemoryManager(memory_config)
    integration = MemoryEnabledLangGraphIntegration(None, memory_manager)
    app = create_memory_enabled_graph(integration, memory_manager, [])

    # Simulate a conversation that exceeds memory limits
    messages = []
    for i in range(8):  # More than max_short_term_messages
        messages.append(HumanMessage(content=f"Message {i+1}: {' '.join(['word'] * (i+1) * 10)}"))  # Longer messages
        messages.append(AIMessage(content=f"Response to message {i+1}"))

        # Add to memory
        await memory_manager.add_interaction("user123", messages[-2:])

    stats = memory_manager.get_memory_health()
    print(f"Total messages in memory: {stats['total_messages']}")
    print(f"Memory efficiency: {stats['memory_efficiency']:.2%}")
    print(f"Strategy: {memory_config.short_term_strategy}")

    # Example 2: Importance-based trimming
    print("\n2. IMPORTANCE_BASED Strategy")
    print("-" * 30)

    memory_config_importance = MemoryConfig(
        max_short_term_messages=5,
        short_term_strategy="importance_based",
        context_window_buffer=2
    )

    memory_manager_importance = MemoryManager(memory_config_importance)
    integration_importance = MemoryEnabledLangGraphIntegration(None, memory_manager_importance)
    app_importance = create_memory_enabled_graph(integration_importance, memory_manager_importance, [])

    # Reset and add messages again
    messages = []
    for i in range(8):
        msg_type = HumanMessage if i % 2 == 0 else AIMessage
        content = f"Message {i+1}: {' '.join(['word'] * (i+1) * 10)}"
        messages.append(msg_type(content=content))
        await memory_manager_importance.add_interaction("user123", messages[-1:])

    stats_importance = memory_manager_importance.get_memory_health()
    print(f"Total messages in memory: {stats_importance['total_messages']}")
    print(f"Average importance score: {stats_importance['average_importance']:.2f}")
    print(f"Strategy: {memory_config_importance.short_term_strategy}")

    # Example 3: Trim middle strategy
    print("\n3. TRIM_MIDDLE Strategy")
    print("-" * 30)

    memory_config_middle = MemoryConfig(
        max_short_term_messages=6,
        short_term_strategy="trim_middle",
        context_window_buffer=2
    )

    memory_manager_middle = MemoryManager(memory_config_middle)
    integration_middle = MemoryEnabledLangGraphIntegration(None, memory_manager_middle)
    app_middle = create_memory_enabled_graph(integration_middle, memory_manager_middle, [])

    # Add many messages to trigger trimming
    messages = []
    for i in range(12):
        messages.append(HumanMessage(content=f"Middle trim test message {i+1}"))
        await memory_manager_middle.add_interaction("user123", messages[-1:])

    stats_middle = memory_manager_middle.get_memory_health()
    print(f"Total messages in memory: {stats_middle['total_messages']}")
    print(f"Strategy: {memory_config_middle.short_term_strategy}")
    print("Note: Keeps beginning and end messages, removes middle")

    # Example 4: Memory consolidation
    print("\n4. Memory Consolidation")
    print("-" * 30)

    consolidation_result = await memory_manager.consolidate_memory()
    print(f"Consolidation completed: {consolidation_result}")

    print("\n" + "=" * 60)
    print("Memory Strategy Comparison Summary:")
    print("=" * 60)
    print(f"Trim Last     : {stats['total_messages']} messages, efficiency: {stats['memory_efficiency']:.1%}")
    print(f"Importance    : {stats_importance['total_messages']} messages, avg importance: {stats_importance['average_importance']:.2f}")
    print(f"Trim Middle   : {stats_middle['total_messages']} messages")
    print("\nEach strategy balances memory usage with information preservation differently.")


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
    llm = MockLLM()
    integration = MemoryEnabledLangGraphIntegration(None, memory_manager, llm)

    conversation_history = []

    while True:
        user_input = input("\nYou: ").strip()

        if user_input.lower() in ['quit', 'exit', 'q']:
            print("Goodbye!")
            break

        if user_input.lower() == 'stats':
            stats = memory_manager.get_memory_health()
            print(f"\nMemory Statistics:")
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