"""
Unit tests for distributed memory implementations.

Tests both DragonDDictStore and DragonCheckpointSaver with fallback behavior.
"""

import pytest
import uuid
from datetime import datetime
from typing import Dict, Any

from flowgentic.langGraph.distributed_memory import (
    DragonDDictStore,
    DragonCheckpointSaver,
    DRAGON_AVAILABLE,
)
from flowgentic.langGraph.memory import (
    DistributedMemoryConfig,
    DistributedMemoryManager,
    LangraphMemoryManager,
)

from langgraph.store.base import PutOp, GetOp


class TestDragonDDictStore:
    """Test suite for DragonDDictStore."""

    def test_initialization(self):
        """Test store initialization."""
        store = DragonDDictStore(n_nodes=1)
        assert store is not None
        assert hasattr(store, "ddict")
        # Should work regardless of Dragon availability
        assert store.using_dragon == DRAGON_AVAILABLE

    def test_put_and_get(self):
        """Test basic put and get operations."""
        store = DragonDDictStore(n_nodes=1)

        # Put an item
        store.put(
            namespace=("user_123",),
            key="profile",
            value={"name": "Alice", "age": 30},
        )

        # Get the item
        item = store.get(namespace=("user_123",), key="profile")
        assert item is not None
        assert item.value["name"] == "Alice"
        assert item.value["age"] == 30
        assert item.key == "profile"
        assert item.namespace == ("user_123",)

    def test_update(self):
        """Test updating existing items."""
        store = DragonDDictStore(n_nodes=1)

        # Initial put
        store.put(
            namespace=("user_123",),
            key="profile",
            value={"name": "Alice", "age": 30},
        )

        # Update
        store.put(
            namespace=("user_123",),
            key="profile",
            value={"name": "Alice", "age": 31},
        )

        # Verify update
        item = store.get(namespace=("user_123",), key="profile")
        assert item.value["age"] == 31
        # created_at should be preserved
        assert item.created_at is not None

    def test_multiple_namespaces(self):
        """Test isolation between different namespaces."""
        store = DragonDDictStore(n_nodes=1)

        # Put items in different namespaces
        store.put(
            namespace=("user_123",),
            key="profile",
            value={"name": "Alice", "age": 30},
        )
        store.put(
            namespace=("user_456",),
            key="profile",
            value={"name": "Bob", "age": 25},
        )

        # Verify isolation
        item1 = store.get(namespace=("user_123",), key="profile")
        item2 = store.get(namespace=("user_456",), key="profile")
        assert item1.value["name"] == "Alice"
        assert item2.value["name"] == "Bob"

    def test_search(self):
        """Test search functionality."""
        store = DragonDDictStore(n_nodes=1)

        # Put multiple items
        store.put(
            namespace=("user_123",),
            key="profile",
            value={"name": "Alice", "age": 30},
        )
        store.put(
            namespace=("user_123",),
            key="settings",
            value={"theme": "dark"},
        )

        # Search by namespace prefix
        results = store.search(namespace_prefix=("user_123",))
        assert len(results) >= 2

    def test_batch_operations(self):
        """Test batch operations."""
        store = DragonDDictStore(n_nodes=1)

        # Execute batch operations
        batch_results = store.batch(
            [
                PutOp(
                    namespace=("user_789",),
                    key="name",
                    value={"name": "Charlie"},
                ),
                GetOp(namespace=("user_789",), key="name"),
            ]
        )

        assert len(batch_results) == 2
        assert batch_results[0] is None  # Put returns None
        assert batch_results[1] is not None  # Get returns item
        assert batch_results[1].value["name"] == "Charlie"

    def test_delete(self):
        """Test delete functionality."""
        store = DragonDDictStore(n_nodes=1)

        # Put and then delete
        store.put(
            namespace=("user_123",),
            key="profile",
            value={"name": "Alice", "age": 30},
        )
        store.delete(namespace=("user_123",), key="profile")

        # Verify deletion
        item = store.get(namespace=("user_123",), key="profile")
        assert item is None

    def test_get_nonexistent(self):
        """Test getting non-existent items."""
        store = DragonDDictStore(n_nodes=1)
        item = store.get(namespace=("nonexistent",), key="key")
        assert item is None

    def test_close(self):
        """Test resource cleanup."""
        store = DragonDDictStore(n_nodes=1)
        # Should not raise an error
        store.close()


class TestDragonCheckpointSaver:
    """Test suite for DragonCheckpointSaver."""

    def test_initialization(self):
        """Test checkpointer initialization."""
        checkpointer = DragonCheckpointSaver(n_nodes=1)
        assert checkpointer is not None
        assert hasattr(checkpointer, "checkpoints")
        assert hasattr(checkpointer, "writes")
        assert checkpointer.using_dragon == DRAGON_AVAILABLE

    def test_save_checkpoint(self):
        """Test saving a checkpoint."""
        checkpointer = DragonCheckpointSaver(n_nodes=1)

        config = {
            "configurable": {"thread_id": "conversation_123", "checkpoint_ns": ""}
        }

        checkpoint = {
            "id": str(uuid.uuid4()),
            "v": 1,
            "ts": datetime.utcnow().isoformat(),
            "channel_values": {"messages": ["Hello", "How are you?"]},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        }

        metadata = {"step": 1, "source": "test"}

        saved_config = checkpointer.put(config, checkpoint, metadata, {})
        assert "checkpoint_id" in saved_config["configurable"]

    def test_retrieve_checkpoint(self):
        """Test retrieving a saved checkpoint."""
        checkpointer = DragonCheckpointSaver(n_nodes=1)

        config = {
            "configurable": {"thread_id": "conversation_123", "checkpoint_ns": ""}
        }

        checkpoint = {
            "id": str(uuid.uuid4()),
            "v": 1,
            "ts": datetime.utcnow().isoformat(),
            "channel_values": {"messages": ["Hello", "How are you?"]},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        }

        metadata = {"step": 1, "source": "test"}

        saved_config = checkpointer.put(config, checkpoint, metadata, {})

        # Retrieve checkpoint
        retrieved = checkpointer.get_tuple(saved_config)
        assert retrieved is not None
        assert retrieved.checkpoint["id"] == checkpoint["id"]
        assert len(retrieved.checkpoint["channel_values"]["messages"]) == 2

    def test_multiple_checkpoints(self):
        """Test multiple checkpoints for the same thread."""
        checkpointer = DragonCheckpointSaver(n_nodes=1)

        config = {
            "configurable": {"thread_id": "conversation_123", "checkpoint_ns": ""}
        }

        # Save first checkpoint
        checkpoint1 = {
            "id": str(uuid.uuid4()),
            "v": 1,
            "ts": datetime.utcnow().isoformat(),
            "channel_values": {"messages": ["Hello", "How are you?"]},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        }
        saved_config1 = checkpointer.put(config, checkpoint1, {"step": 1}, {})

        # Save second checkpoint
        checkpoint2 = {
            "id": str(uuid.uuid4()),
            "v": 1,
            "ts": datetime.utcnow().isoformat(),
            "channel_values": {"messages": ["Hello", "How are you?", "I'm fine"]},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        }
        saved_config2 = checkpointer.put(config, checkpoint2, {"step": 2}, {})

        # Get latest checkpoint
        latest = checkpointer.get_tuple(config)
        assert latest is not None
        assert latest.checkpoint["id"] == checkpoint2["id"]

        # Get specific checkpoint
        specific = checkpointer.get_tuple(saved_config1)
        assert specific is not None
        assert specific.checkpoint["id"] == checkpoint1["id"]

    def test_multiple_threads(self):
        """Test isolation between different threads."""
        checkpointer = DragonCheckpointSaver(n_nodes=1)

        config_thread1 = {
            "configurable": {"thread_id": "conversation_123", "checkpoint_ns": ""}
        }
        config_thread2 = {
            "configurable": {"thread_id": "conversation_456", "checkpoint_ns": ""}
        }

        checkpoint_thread1 = {
            "id": str(uuid.uuid4()),
            "v": 1,
            "ts": datetime.utcnow().isoformat(),
            "channel_values": {"messages": ["Thread 1"]},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        }

        checkpoint_thread2 = {
            "id": str(uuid.uuid4()),
            "v": 1,
            "ts": datetime.utcnow().isoformat(),
            "channel_values": {"messages": ["Thread 2"]},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        }

        checkpointer.put(config_thread1, checkpoint_thread1, {"step": 1}, {})
        checkpointer.put(config_thread2, checkpoint_thread2, {"step": 1}, {})

        thread1_latest = checkpointer.get_tuple(config_thread1)
        thread2_latest = checkpointer.get_tuple(config_thread2)

        assert thread1_latest.checkpoint["id"] != thread2_latest.checkpoint["id"]
        assert (
            thread1_latest.checkpoint["channel_values"]["messages"][0] == "Thread 1"
        )
        assert (
            thread2_latest.checkpoint["channel_values"]["messages"][0] == "Thread 2"
        )

    def test_close(self):
        """Test resource cleanup."""
        checkpointer = DragonCheckpointSaver(n_nodes=1)
        # Should not raise an error
        checkpointer.close()


class TestDistributedMemoryConfig:
    """Test suite for DistributedMemoryConfig."""

    def test_default_config(self):
        """Test default configuration."""
        config = DistributedMemoryConfig()
        assert config.mode == "auto"
        assert config.n_nodes == 1
        assert config.managers_per_node == 1
        assert config.enable_store is True
        assert config.enable_checkpointer is True
        assert config.fallback_to_local is True

    def test_local_mode(self):
        """Test local mode configuration."""
        config = DistributedMemoryConfig(mode="local")
        assert config.should_use_distributed() is False

    def test_distributed_mode(self):
        """Test distributed mode configuration."""
        config = DistributedMemoryConfig(mode="distributed")
        assert config.should_use_distributed() is True

    def test_auto_mode(self, monkeypatch):
        """Test auto mode with environment detection."""
        config = DistributedMemoryConfig(mode="auto")

        # Without HPC environment variables
        assert config.should_use_distributed() is False

        # With HPC environment variable
        monkeypatch.setenv("SLURM_JOB_ID", "12345")
        config_with_env = DistributedMemoryConfig(mode="auto")
        assert config_with_env.should_use_distributed() is True


class TestDistributedMemoryManager:
    """Test suite for DistributedMemoryManager."""

    def test_initialization(self):
        """Test manager initialization."""
        manager = DistributedMemoryManager()
        assert manager is not None
        assert manager._initialized is False

    def test_local_mode(self):
        """Test manager in local mode."""
        config = DistributedMemoryConfig(mode="local")
        manager = DistributedMemoryManager(config)

        store = manager.get_store()
        checkpointer = manager.get_checkpointer()

        assert store is None
        assert checkpointer is None
        assert manager.is_distributed() is False

    def test_memory_info(self):
        """Test getting memory information."""
        config = DistributedMemoryConfig(mode="local")
        manager = DistributedMemoryManager(config)

        info = manager.get_memory_info()
        assert "mode" in info
        assert "is_distributed" in info
        assert "store_enabled" in info
        assert "checkpointer_enabled" in info
        assert info["mode"] == "local"
        assert info["is_distributed"] is False

    def test_close(self):
        """Test resource cleanup."""
        manager = DistributedMemoryManager()
        # Should not raise an error
        manager.close()


class TestLangraphMemoryManagerDistributed:
    """Test suite for LangraphMemoryManager with distributed support."""

    def test_initialization_with_distributed_config(self):
        """Test initialization with distributed configuration."""
        distributed_config = DistributedMemoryConfig(mode="local")
        manager = LangraphMemoryManager(distributed_config=distributed_config)

        assert manager is not None
        assert manager.distributed_config is not None
        assert manager.distributed_config.mode == "local"

    def test_get_store(self):
        """Test getting distributed store."""
        distributed_config = DistributedMemoryConfig(mode="local")
        manager = LangraphMemoryManager(distributed_config=distributed_config)

        store = manager.get_store()
        # In local mode, store should be None
        assert store is None

    def test_get_checkpointer(self):
        """Test getting distributed checkpointer."""
        distributed_config = DistributedMemoryConfig(mode="local")
        manager = LangraphMemoryManager(distributed_config=distributed_config)

        checkpointer = manager.get_checkpointer()
        # In local mode, checkpointer should be None
        assert checkpointer is None

    def test_is_distributed(self):
        """Test checking if distributed memory is active."""
        distributed_config = DistributedMemoryConfig(mode="local")
        manager = LangraphMemoryManager(distributed_config=distributed_config)

        assert manager.is_distributed() is False

    def test_full_memory_info(self):
        """Test getting comprehensive memory information."""
        distributed_config = DistributedMemoryConfig(mode="local")
        manager = LangraphMemoryManager(distributed_config=distributed_config)

        info = manager.get_full_memory_info()
        assert "short_term" in info
        assert "distributed" in info
        assert "mode" in info["distributed"]

    def test_close(self):
        """Test resource cleanup."""
        manager = LangraphMemoryManager()
        # Should not raise an error
        manager.close()


class TestIntegration:
    """Integration tests combining multiple components."""

    def test_end_to_end_workflow(self):
        """Test complete workflow with store and checkpointer."""
        # Create backends
        store = DragonDDictStore(n_nodes=1)
        checkpointer = DragonCheckpointSaver(n_nodes=1)

        # Store long-term memory
        store.put(
            namespace=("test_thread",),
            key="user_name",
            value={"name": "Test User"},
        )

        # Save checkpoint
        config = {
            "configurable": {"thread_id": "test_thread", "checkpoint_ns": ""}
        }

        checkpoint = {
            "id": str(uuid.uuid4()),
            "v": 1,
            "ts": datetime.utcnow().isoformat(),
            "channel_values": {"messages": ["Hello", "World"]},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        }

        saved_config = checkpointer.put(config, checkpoint, {"step": 1}, {})

        # Retrieve both
        memory = store.get(namespace=("test_thread",), key="user_name")
        retrieved_checkpoint = checkpointer.get_tuple(saved_config)

        assert memory.value["name"] == "Test User"
        assert retrieved_checkpoint.checkpoint["id"] == checkpoint["id"]

        # Cleanup
        store.close()
        checkpointer.close()

    def test_manager_integration(self):
        """Test integration through memory managers."""
        distributed_config = DistributedMemoryConfig(mode="local")
        manager = LangraphMemoryManager(distributed_config=distributed_config)

        # Get memory info
        info = manager.get_full_memory_info()

        assert info is not None
        assert "short_term" in info
        assert "distributed" in info

        # Cleanup
        manager.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
