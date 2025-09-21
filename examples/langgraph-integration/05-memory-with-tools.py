"""
Memory-Enabled Tools Integration Example

This example demonstrates how to integrate FlowGentic memory management with
AsyncFlow tools in LangGraph workflows. It shows:

1. Memory-enabled tool execution
2. Context-aware tool responses
3. Memory state persistence across tool calls
4. Performance monitoring with memory statistics

FEATURES:
    [X] Memory-enabled tool integration
    [X] AsyncFlow backend with memory
    [X] Context-aware tool responses
    [X] Memory statistics during tool execution
    [ ] Real LLM integration (mocked for demonstration)
"""

import asyncio
import os
from typing import Annotated, List, Dict, Any, Optional
from pydantic import BaseModel
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_core.tools import tool

# Import FlowGentic components
from flowgentic.langGraph import (
    LangGraphIntegration,
    MemoryEnabledLangGraphIntegration,
    MemoryManager,
    MemoryConfig,
    MemoryEnabledState,
    create_memory_enabled_graph
)

# For AsyncFlow backend
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine
from concurrent.futures import ThreadPoolExecutor

load_dotenv()


# Define memory-enabled state
class ToolMemoryState(BaseModel):
    """State for tool-enabled workflows with memory."""
    model_config = {"arbitrary_types_allowed": True}

    messages: Annotated[List[BaseMessage], lambda x, y: x + y]
    memory_context: Dict[str, Any] = {}
    tool_results: List[Dict[str, Any]] = []


# Define AsyncFlow tools with memory awareness
@tool
async def weather_tool(city: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Get weather information for a city with memory context."""
    await asyncio.sleep(0.1)  # Simulate API call

    # Use memory context if available
    user_preferences = context.get("user_preferences", {}) if context else {}
    temperature_unit = user_preferences.get("temperature_unit", "celsius")

    # Mock weather data
    base_temp_c = 22
    if "san francisco" in city.lower():
        base_temp_c = 18
    elif "new york" in city.lower():
        base_temp_c = 25

    result = {
        "city": city,
        "temperature_celsius": base_temp_c,
        "temperature_fahrenheit": base_temp_c * 9/5 + 32,
        "humidity_percentage": 65,
        "condition": "Partly cloudy",
        "unit_used": temperature_unit
    }

    return result


@tool
async def calculator_tool(expression: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Calculate mathematical expressions with memory context."""
    await asyncio.sleep(0.05)  # Simulate calculation

    try:
        # Safe evaluation (in production, use a proper math parser)
        result = eval(expression, {"__builtins__": {}}, {})
        return {
            "expression": expression,
            "result": result,
            "success": True
        }
    except Exception as e:
        return {
            "expression": expression,
            "error": str(e),
            "success": False
        }


@tool
async def memory_search_tool(query: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Search memory for relevant information."""
    # This tool demonstrates accessing memory context
    memory_data = context.get("memory_stats", {}) if context else {}

    return {
        "query": query,
        "memory_messages": memory_data.get("total_messages", 0),
        "memory_efficiency": memory_data.get("memory_efficiency", 0),
        "relevant_found": bool(query.lower() in ["weather", "calculation", "memory"])
    }


async def create_memory_enabled_workflow():
    """Create a workflow with memory-enabled tools."""

    # Initialize AsyncFlow backend
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor(max_workers=4))
    flow = await WorkflowEngine.create(backend=backend)

    # Configure memory
    memory_config = MemoryConfig(
        max_short_term_messages=15,
        short_term_strategy="importance_based",
        context_window_buffer=3
    )

    memory_manager = MemoryManager(memory_config)

    # Create memory-enabled integration
    integration = MemoryEnabledLangGraphIntegration(backend, memory_manager)
    integration.flow = flow  # Manually set the flow since we already created it

    # Register tools
    @integration.asyncflow_tool
    async def get_weather(city: str) -> Dict[str, Any]:
        """Get weather for a city."""
        context = {"temperature_unit": "celsius"}  # Could come from memory
        return await weather_tool(city, context)

    @integration.asyncflow_tool
    async def calculate(expression: str) -> Dict[str, Any]:
        """Calculate a mathematical expression."""
        return await calculator_tool(expression)

    @integration.asyncflow_tool
    async def search_memory(query: str) -> Dict[str, Any]:
        """Search memory for information."""
        # Get current memory context
        memory_stats = memory_manager.get_memory_health()
        context = {"memory_stats": memory_stats}
        return await memory_search_tool(query, context)

    tools = [get_weather, calculate, search_memory]

    return integration, memory_manager, tools


async def demonstrate_memory_with_tools():
    """Demonstrate memory-enabled tool execution."""

    print("=" * 70)
    print("Memory-Enabled Tools Integration Example")
    print("=" * 70)

    # Setup workflow
    integration, memory_manager, tools = await create_memory_enabled_workflow()

    print("\n1. Tool Registration and Memory Setup")
    print("-" * 40)
    print(f"Registered {len(tools)} tools with memory integration")
    print(f"Memory strategy: {memory_manager.config.short_term_strategy}")
    print(f"Max messages: {memory_manager.config.max_short_term_messages}")

    # Simulate a conversation with tool usage
    print("\n2. Conversation with Tool Usage")
    print("-" * 35)

    conversation = [
        "What's the weather like in San Francisco?",
        "Calculate 15 * 23 + 7",
        "What's my name? I think I told you earlier.",
        "Search memory for weather information",
        "Calculate the square root of 144",
    ]

    for i, user_message in enumerate(conversation, 1):
        print(f"\n--- Interaction {i} ---")
        print(f"User: {user_message}")

        # Add user message to memory
        await memory_manager.add_interaction(
            "demo_user",
            [HumanMessage(content=user_message)]
        )

        # Get memory context for tool execution
        context = await memory_manager.get_relevant_context("demo_user", user_message)
        memory_stats = memory_manager.get_memory_health()

        print(f"Memory state: {memory_stats['total_messages']} messages")
        print(f"Memory efficiency: {memory_stats['memory_efficiency']:.1%}")

        # Simulate tool decision and execution
        if "weather" in user_message.lower():
            tool_name = "get_weather"
            tool_args = {"city": "San Francisco"}
        elif "calculate" in user_message.lower() or any(op in user_message for op in ["*", "+", "-", "/"]):
            tool_name = "calculate"
            # Extract expression (simplified)
            tool_args = {"expression": user_message.split("calculate", 1)[-1].strip() if "calculate" in user_message else "15*23+7"}
        elif "search" in user_message.lower():
            tool_name = "search_memory"
            tool_args = {"query": "weather"}
        else:
            tool_name = None
            tool_args = {}

        if tool_name:
            print(f"Executing tool: {tool_name} with args: {tool_args}")

            # Simulate tool execution (in real scenario, this would be handled by LangGraph)
            if tool_name == "get_weather":
                result = {"city": "San Francisco", "temperature_celsius": 18, "condition": "Foggy"}
            elif tool_name == "calculate":
                result = {"expression": tool_args["expression"], "result": eval(tool_args["expression"]), "success": True}
            elif tool_name == "search_memory":
                result = {"query": tool_args["query"], "memory_messages": memory_stats["total_messages"], "relevant_found": True}

            print(f"Tool result: {result}")

            # Add tool result to memory
            tool_message = AIMessage(content=f"Tool result: {result}")
            await memory_manager.add_interaction("demo_user", [tool_message])

        # Show memory growth
        updated_stats = memory_manager.get_memory_health()
        print(f"After tool execution: {updated_stats['total_messages']} messages in memory")

    print("\n3. Final Memory Analysis")
    print("-" * 25)

    final_stats = memory_manager.get_memory_health()
    final_messages = memory_manager.get_short_term_messages()

    print(f"Final memory contains: {final_stats['total_messages']} messages")
    print(f"Average importance: {final_stats['average_importance']:.2f}")
    print(f"Total interactions: {final_stats['interaction_count']}")

    print("\nMessage types in memory:")
    message_counts = {}
    for msg in final_messages:
        msg_type = type(msg).__name__
        message_counts[msg_type] = message_counts.get(msg_type, 0) + 1

    for msg_type, count in message_counts.items():
        print(f"  {msg_type}: {count} messages")

    print("\n4. Context Retrieval Demonstration")
    print("-" * 38)

    test_queries = ["weather", "calculation", "memory"]

    for query in test_queries:
        context = await memory_manager.get_relevant_context("demo_user", query)
        relevant_count = len(context.get("relevant_messages", []))
        print(f"Query '{query}': found {relevant_count} relevant messages from {len(context.get('short_term', []))} total")

    print("\n" + "=" * 70)
    print("Memory-Enabled Tools Benefits:")
    print("=" * 70)
    print("✓ Tools have access to conversation context")
    print("✓ Memory persists across tool executions")
    print("✓ Context-aware tool responses")
    print("✓ Memory statistics for monitoring")
    print("✓ Automatic memory management during tool workflows")


async def demonstrate_memory_consolidation():
    """Show memory consolidation during tool-heavy workflows."""

    print("\n" + "=" * 70)
    print("Memory Consolidation with Tools")
    print("=" * 70)

    # Setup with smaller memory limit to trigger consolidation
    memory_config = MemoryConfig(
        max_short_term_messages=8,
        short_term_strategy="importance_based",
        context_window_buffer=2
    )

    memory_manager = MemoryManager(memory_config)

    print("Simulating tool-heavy conversation that triggers memory consolidation...")

    # Simulate many tool interactions
    for i in range(15):
        # Alternate between different types of interactions
        if i % 3 == 0:
            # Weather queries
            user_msg = HumanMessage(content=f"What's the weather in city {i}?")
            tool_result = AIMessage(content=f"Weather data for city {i}")
        elif i % 3 == 1:
            # Calculations
            user_msg = HumanMessage(content=f"Calculate {i} * {i+1}")
            tool_result = AIMessage(content=f"Result: {i * (i+1)}")
        else:
            # Memory searches
            user_msg = HumanMessage(content=f"Search for info about item {i}")
            tool_result = AIMessage(content=f"Found information about item {i}")

        # Add interaction
        await memory_manager.add_interaction("tool_user", [user_msg, tool_result])

        # Periodic consolidation
        if (i + 1) % 5 == 0:
            consolidation = await memory_manager.consolidate_memory()
            stats = memory_manager.get_memory_health()
            print(f"After {i+1} interactions: {stats['total_messages']} messages (consolidated)")

    final_stats = memory_manager.get_memory_health()
    print(f"\nFinal result: {final_stats['total_messages']} messages maintained")
    print(f"Memory efficiency: {final_stats['memory_efficiency']:.1%}")
    print("Consolidation preserved important tool interactions!")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--consolidation":
        asyncio.run(demonstrate_memory_consolidation())
    else:
        asyncio.run(demonstrate_memory_with_tools())