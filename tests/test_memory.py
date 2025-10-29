"""
Unit tests for memory management system.
"""

import asyncio
import pytest
from typing import List, cast
from datetime import datetime
from unittest.mock import Mock
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage
from langchain_core.language_models import BaseChatModel

from flowgentic.langGraph.memory import (
    MemoryConfig,
    ShortTermMemoryItem,
    ShortTermMemoryManager,
    MemoryManager
)


def test_memory_config():
    """Test MemoryConfig class."""
    # Test default configuration values
    config = MemoryConfig()
    assert config.max_short_term_messages == 50
    assert config.short_term_strategy == "trim_last"
    assert config.context_window_buffer == 10
    assert config.memory_update_threshold == 5

    # Test custom configuration values
    config = MemoryConfig(
        max_short_term_messages=100,
        short_term_strategy="trim_middle",
        context_window_buffer=20,
        memory_update_threshold=10
    )
    assert config.max_short_term_messages == 100
    assert config.short_term_strategy == "trim_middle"
    assert config.context_window_buffer == 20
    assert config.memory_update_threshold == 10


def test_short_term_memory_item():
    """Test ShortTermMemoryItem class."""
    # Test creating memory item from system message
    msg = SystemMessage(content="You are a helpful assistant")
    item = ShortTermMemoryItem.from_message(msg)

    assert item.content == "You are a helpful assistant"
    assert item.message_type == "SystemMessage"
    assert abs(item.importance - 2.0) < 0.1  # System messages have higher importance (with length bonus)

    # Test creating memory item from human message
    msg = HumanMessage(content="Hello, how are you?")
    item = ShortTermMemoryItem.from_message(msg)

    assert item.content == "Hello, how are you?"
    assert item.message_type == "HumanMessage"
    assert abs(item.importance - 1.2) < 0.1  # Human messages have moderate importance (with length bonus)

    # Test creating memory item from AI message
    msg = AIMessage(content="I'm doing well, thank you!")
    item = ShortTermMemoryItem.from_message(msg)

    assert item.content == "I'm doing well, thank you!"
    assert item.message_type == "AIMessage"
    assert abs(item.importance - 1.0) < 0.1  # Base importance (with length bonus)

    # Test importance calculation for long messages
    long_content = "A" * 1500  # Long message
    msg = HumanMessage(content=long_content)
    item = ShortTermMemoryItem.from_message(msg)

    # Should have length bonus (1.2 base + up to 1.0 bonus)
    assert item.importance > 1.2


def test_short_term_memory_manager():
    """Test ShortTermMemoryManager class."""
    # Test manager initialization
    config = MemoryConfig(max_short_term_messages=10)
    manager = ShortTermMemoryManager(config)

    assert len(manager.message_history) == 0
    assert len(manager.memory_items) == 0
    assert manager.interaction_count == 0

    # Test adding messages to memory
    messages = cast(List[BaseMessage], [
        SystemMessage(content="System prompt"),
        HumanMessage(content="Hello"),
        AIMessage(content="Hi there!")
    ])

    result = manager.add_messages(messages)

    assert len(result) == 3
    assert len(manager.memory_items) == 3
    assert manager.interaction_count == 1

    # Test trimming from end strategy
    config = MemoryConfig(max_short_term_messages=2, short_term_strategy="trim_last")
    manager = ShortTermMemoryManager(config)

    messages = cast(List[BaseMessage], [
        SystemMessage(content="System prompt"),
        HumanMessage(content="Message 1"),
        HumanMessage(content="Message 2"),
        HumanMessage(content="Message 3")
    ])

    manager.add_messages(messages)

    # Should keep system message + 1 most recent message
    assert len(manager.message_history) == 2
    assert isinstance(manager.message_history[0], SystemMessage)
    assert manager.message_history[1].content == "Message 3"

    # Test getting memory statistics
    config = MemoryConfig(max_short_term_messages=10)
    manager = ShortTermMemoryManager(config)

    messages = cast(List[BaseMessage], [
        SystemMessage(content="System"),
        HumanMessage(content="Human"),
        AIMessage(content="AI")
    ])

    manager.add_messages(messages)
    stats = manager.get_memory_stats()

    assert stats["total_messages"] == 3
    assert stats["system_messages"] == 1
    assert stats["human_messages"] == 1
    assert stats["ai_messages"] == 1
    assert stats["interaction_count"] == 1

    # Test clearing memory
    manager.clear()

    assert len(manager.message_history) == 0
    assert len(manager.memory_items) == 0
    assert manager.interaction_count == 0


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_memory_manager():
    """Test MemoryManager class."""
    # Test manager initialization
    config = MemoryConfig()
    manager = MemoryManager(config)

    assert isinstance(manager.short_term_manager, ShortTermMemoryManager)

    # Test adding interactions
    messages = cast(List[BaseMessage], [HumanMessage(content="Test message")])
    metadata = {"test": "value"}

    result = await manager.add_interaction("user123", messages, metadata)

    assert result["short_term_messages"] == 1
    assert result["total_interactions"] == 1

    # Test getting relevant context
    messages = cast(List[BaseMessage], [
        SystemMessage(content="System prompt"),
        HumanMessage(content="Hello"),
    ])
    await manager.add_interaction("user123", messages)

    context = await manager.get_relevant_context("user123")

    assert "short_term" in context
    assert "memory_stats" in context
    assert "relevant_messages" in context
    assert len(context["short_term"]) == 3  # From previous interaction + new ones

    # Test getting memory health statistics
    config = MemoryConfig(max_short_term_messages=10)
    manager = MemoryManager(config)

    messages = cast(List[BaseMessage], [HumanMessage(content="Test")])
    manager.short_term_manager.add_messages(messages)

    health = manager.get_memory_health()

    assert "config" in health
    assert "memory_efficiency" in health
    assert "average_importance" in health
    assert health["memory_efficiency"] == 0.1  # 1/10


def test_memory_summarization():
    """Test memory summarization functionality."""
    # Create a mock LLM
    mock_llm = Mock(spec=BaseChatModel)
    mock_response = Mock()
    mock_response.content = "This is a summary of the conversation."
    mock_llm.invoke.return_value = mock_response

    # Configure memory with summarization
    config = MemoryConfig(
        max_short_term_messages=3,
        short_term_strategy="summarize",
        enable_summarization=True
    )

    manager = ShortTermMemoryManager(config, mock_llm)

    # Add messages that will trigger summarization
    messages = cast(List[BaseMessage], [
        HumanMessage(content="First message"),
        AIMessage(content="First response"),
        HumanMessage(content="Second message"),
        AIMessage(content="Second response"),
        HumanMessage(content="Third message"),  # This should trigger summarization
    ])

    manager.add_messages(messages)

    # Should have system messages + summary + recent messages
    # Since we have no system messages, it should have summary + recent messages
    assert len(manager.message_history) <= 3

    # Check that LLM was called for summarization
    assert mock_llm.invoke.called

    # Test fallback when no LLM
    config_no_llm = MemoryConfig(
        max_short_term_messages=2,
        short_term_strategy="summarize",
        enable_summarization=False
    )
    manager_no_llm = ShortTermMemoryManager(config_no_llm)

    messages_trim = cast(List[BaseMessage], [
        HumanMessage(content="Message 1"),
        HumanMessage(content="Message 2"),
        HumanMessage(content="Message 3"),
    ])

    manager_no_llm.add_messages(messages_trim)
    # Should fall back to trimming
    assert len(manager_no_llm.message_history) == 2


async def run_async_tests():
    """Run async tests."""
    await test_memory_manager()


if __name__ == "__main__":
    # Run synchronous tests
    test_memory_config()
    test_short_term_memory_item()
    test_short_term_memory_manager()
    test_memory_summarization()

    # Run asynchronous tests
    asyncio.run(run_async_tests())

    print("All tests passed!")