"""
Example demonstrating distributed memory usage with Dragon DDict.

This example shows how to:
1. Configure distributed memory for HPC environments
2. Use distributed store for long-term memory
3. Use distributed checkpointer for graph state persistence
4. Integrate with LangGraph agents

The system automatically falls back to local memory if Dragon is not available.
"""

import asyncio
from typing import Annotated, TypedDict
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.graph.state import CompiledStateGraph

# Import flowgentic components
from flowgentic.langGraph.memory import (
    MemoryConfig,
    DistributedMemoryConfig,
    LangraphMemoryManager,
)


# Define agent state
class AgentState(TypedDict):
    """State for a simple conversational agent."""
    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    conversation_count: int


def create_agent_node(memory_manager: LangraphMemoryManager):
    """Create an agent node that uses distributed memory."""
    
    def agent(state: AgentState) -> AgentState:
        """Simple agent that echoes messages and tracks conversation count."""
        messages = state.get("messages", [])
        user_id = state.get("user_id", "default_user")
        count = state.get("conversation_count", 0)
        
        # Get distributed store if available
        store = memory_manager.get_store()
        
        # Store/retrieve long-term memory
        if store:
            # Store user preferences in distributed memory
            store.put(
                namespace=(user_id,),
                key="preferences",
                value={"conversation_count": count + 1}
            )
            
            # Retrieve user history
            history = store.get(namespace=(user_id,), key="preferences")
            if history:
                print(f"📊 Retrieved from distributed store: {history.value}")
        
        # Process last message
        if messages:
            last_message = messages[-1]
            if isinstance(last_message, HumanMessage):
                response = AIMessage(
                    content=f"Echo: {last_message.content} (conversation #{count + 1})"
                )
                return {
                    "messages": [response],
                    "user_id": user_id,
                    "conversation_count": count + 1,
                }
        
        return state
    
    return agent


async def example_local_mode():
    """Example using local (in-memory) mode."""
    print("\n" + "="*60)
    print("Example 1: Local Mode (In-Memory)")
    print("="*60)
    
    # Configure for local mode
    memory_config = MemoryConfig(max_short_term_messages=10)
    distributed_config = DistributedMemoryConfig(mode="local")
    
    memory_manager = LangraphMemoryManager(
        config=memory_config,
        distributed_config=distributed_config,
    )
    
    # Check memory status
    print(f"✓ Distributed memory active: {memory_manager.is_distributed()}")
    
    # Build graph
    builder = StateGraph(AgentState)
    builder.add_node("agent", create_agent_node(memory_manager))
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    
    # Compile with checkpointer (will be None in local mode)
    checkpointer = memory_manager.get_checkpointer()
    graph = builder.compile(checkpointer=checkpointer)
    
    # Run conversation
    config = {"configurable": {"thread_id": "conversation_1"}}
    
    result = graph.invoke(
        {
            "messages": [HumanMessage(content="Hello!")],
            "user_id": "user_123",
            "conversation_count": 0,
        },
        config,
    )
    
    print(f"✓ Agent response: {result['messages'][-1].content}")
    
    # Cleanup
    memory_manager.close()
    print("✓ Local mode example completed\n")


async def example_distributed_mode():
    """Example using distributed mode (requires Dragon)."""
    print("\n" + "="*60)
    print("Example 2: Distributed Mode (Dragon DDict)")
    print("="*60)
    
    # Configure for distributed mode
    memory_config = MemoryConfig(max_short_term_messages=10)
    distributed_config = DistributedMemoryConfig(
        mode="distributed",  # Force distributed mode
        n_nodes=1,
        managers_per_node=1,
        store_total_mem=1 * 1024 * 1024 * 1024,  # 1 GB for store
        checkpoint_total_mem=2 * 1024 * 1024 * 1024,  # 2 GB for checkpoints
        fallback_to_local=True,  # Fall back if Dragon unavailable
    )
    
    memory_manager = LangraphMemoryManager(
        config=memory_config,
        distributed_config=distributed_config,
    )
    
    # Check memory status
    is_distributed = memory_manager.is_distributed()
    print(f"✓ Distributed memory active: {is_distributed}")
    
    if is_distributed:
        print("✓ Using Dragon DDict for distributed storage")
    else:
        print("⚠ Dragon not available - using local fallback")
    
    # Get memory info
    memory_info = memory_manager.get_full_memory_info()
    print(f"✓ Memory configuration: {memory_info['distributed']}")
    
    # Build graph
    builder = StateGraph(AgentState)
    builder.add_node("agent", create_agent_node(memory_manager))
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    
    # Compile with distributed checkpointer
    checkpointer = memory_manager.get_checkpointer()
    store = memory_manager.get_store()
    
    graph = builder.compile(checkpointer=checkpointer, store=store)
    
    # Run multiple conversations
    config1 = {"configurable": {"thread_id": "conversation_1"}}
    config2 = {"configurable": {"thread_id": "conversation_2"}}
    
    # First conversation
    result1 = graph.invoke(
        {
            "messages": [HumanMessage(content="Hello from thread 1!")],
            "user_id": "user_123",
            "conversation_count": 0,
        },
        config1,
    )
    print(f"✓ Thread 1 response: {result1['messages'][-1].content}")
    
    # Second conversation
    result2 = graph.invoke(
        {
            "messages": [HumanMessage(content="Hello from thread 2!")],
            "user_id": "user_456",
            "conversation_count": 0,
        },
        config2,
    )
    print(f"✓ Thread 2 response: {result2['messages'][-1].content}")
    
    # Continue first conversation (checkpoint should be preserved)
    result1_continued = graph.invoke(
        {
            "messages": result1["messages"] + [HumanMessage(content="How are you?")],
            "user_id": "user_123",
            "conversation_count": result1["conversation_count"],
        },
        config1,
    )
    print(f"✓ Thread 1 continued: {result1_continued['messages'][-1].content}")
    
    # Cleanup
    memory_manager.close()
    print("✓ Distributed mode example completed\n")


async def example_auto_mode():
    """Example using auto mode (detects HPC environment)."""
    print("\n" + "="*60)
    print("Example 3: Auto Mode (Environment Detection)")
    print("="*60)
    
    # Configure for auto mode - will detect HPC environment
    memory_config = MemoryConfig(max_short_term_messages=10)
    distributed_config = DistributedMemoryConfig(
        mode="auto",  # Auto-detect based on environment
        n_nodes=1,
        managers_per_node=1,
    )
    
    memory_manager = LangraphMemoryManager(
        config=memory_config,
        distributed_config=distributed_config,
    )
    
    # Check what mode was selected
    is_distributed = memory_manager.is_distributed()
    memory_info = memory_manager.get_full_memory_info()
    
    print(f"✓ Auto-detected mode: {'distributed' if is_distributed else 'local'}")
    print(f"✓ Configuration: {memory_info['distributed']}")
    
    # Build and run graph
    builder = StateGraph(AgentState)
    builder.add_node("agent", create_agent_node(memory_manager))
    builder.add_edge(START, "agent")
    builder.add_edge("agent", END)
    
    checkpointer = memory_manager.get_checkpointer()
    store = memory_manager.get_store()
    graph = builder.compile(checkpointer=checkpointer, store=store)
    
    config = {"configurable": {"thread_id": "auto_conversation"}}
    
    result = graph.invoke(
        {
            "messages": [HumanMessage(content="Testing auto mode!")],
            "user_id": "user_auto",
            "conversation_count": 0,
        },
        config,
    )
    
    print(f"✓ Agent response: {result['messages'][-1].content}")
    
    # Cleanup
    memory_manager.close()
    print("✓ Auto mode example completed\n")


async def example_with_langgraph_integration():
    """Example using LangraphIntegration for full workflow."""
    print("\n" + "="*60)
    print("Example 4: Full Integration with LangraphIntegration")
    print("="*60)
    
    from radical.asyncflow.workflow_manager import LocalBackend
    from flowgentic.langGraph.main import LangraphIntegration
    
    # Configure memory
    memory_config = MemoryConfig(
        max_short_term_messages=50,
        short_term_strategy="trim_last",
    )
    
    distributed_config = DistributedMemoryConfig(
        mode="auto",
        n_nodes=1,
        enable_store=True,
        enable_checkpointer=True,
    )
    
    # Create integration with memory configuration
    backend = LocalBackend()
    
    async with LangraphIntegration(
        backend=backend,
        memory_config=memory_config,
        distributed_config=distributed_config,
    ) as integration:
        
        # Access memory manager
        memory_manager = integration.memory_manager
        
        print(f"✓ Integration initialized")
        print(f"✓ Distributed: {memory_manager.is_distributed()}")
        
        # Get full memory info
        info = memory_manager.get_full_memory_info()
        print(f"✓ Short-term config: {info['short_term']['config']}")
        print(f"✓ Distributed config: {info['distributed']}")
        
        # Build graph with integration's memory
        builder = StateGraph(AgentState)
        builder.add_node("agent", create_agent_node(memory_manager))
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)
        
        checkpointer = memory_manager.get_checkpointer()
        store = memory_manager.get_store()
        graph = builder.compile(checkpointer=checkpointer, store=store)
        
        # Run conversation
        config = {"configurable": {"thread_id": "integration_test"}}
        
        result = graph.invoke(
            {
                "messages": [HumanMessage(content="Testing full integration!")],
                "user_id": "user_integration",
                "conversation_count": 0,
            },
            config,
        )
        
        print(f"✓ Agent response: {result['messages'][-1].content}")
        print("✓ Full integration example completed\n")


async def main():
    """Run all examples."""
    print("\n" + "="*70)
    print("Flowgentic Distributed Memory Examples")
    print("="*70)
    
    try:
        # Run examples
        await example_local_mode()
        await example_distributed_mode()
        await example_auto_mode()
        await example_with_langgraph_integration()
        
        print("\n" + "="*70)
        print("✅ All examples completed successfully!")
        print("="*70)
        
    except Exception as e:
        print(f"\n❌ Error running examples: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
