"""
Distributed memory implementations for LangGraph using Dragon DDict backend.

This module provides high-performance distributed memory storage for HPC environments:
- DragonDDictStore: Distributed key-value store implementing LangGraph's BaseStore
- DragonCheckpointSaver: Distributed checkpoint persistence implementing BaseCheckpointSaver
- Graceful degradation: Falls back to in-memory storage if Dragon is unavailable
"""

from datetime import datetime
from typing import Iterable, Iterator, List, Optional, Sequence, Tuple
import logging

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.store.base import (
    BaseStore,
    DeleteOp,
    GetOp,
    Item,
    ListNamespacesOp,
    Op,
    PutOp,
    SearchOp,
)

logger = logging.getLogger(__name__)


# Try to import Dragon, but don't fail if it's not available
try:
    from dragon.data import DDict
    DRAGON_AVAILABLE = True
    logger.info("Dragon DDict is available for distributed memory")
except ImportError:
    DRAGON_AVAILABLE = False
    logger.warning("Dragon DDict not available - distributed memory will fall back to in-memory storage")
    DDict = None


class DragonDDictStore(BaseStore):
    """
    Distributed memory store for LangGraph using Dragon DDict backend.
    Provides high-performance distributed key-value storage across nodes.
    
    Falls back to in-memory dictionary if Dragon is not available.
    """

    def __init__(
        self,
        managers_per_node: int = 1,
        n_nodes: int = 1,
        total_mem: int = 10 * 1024 * 1024 * 1024,  # 10 GB default
        **kwargs,
    ):
        """
        Initialize Dragon DDict store.

        Args:
            managers_per_node: Number of managers per node
            n_nodes: Number of nodes for distribution
            total_mem: Total memory in bytes across all managers
            **kwargs: Additional DDict parameters
        """
        if DRAGON_AVAILABLE and DDict is not None:
            try:
                self.ddict = DDict(
                    managers_per_node=managers_per_node,
                    n_nodes=n_nodes,
                    total_mem=total_mem,
                    **kwargs,
                )
                self.using_dragon = True
                logger.info(f"Initialized DragonDDictStore with {n_nodes} nodes, {total_mem / (1024**3):.2f} GB total memory")
            except Exception as e:
                logger.warning(f"Failed to initialize Dragon DDict: {e}. Falling back to in-memory storage.")
                self.ddict = {}
                self.using_dragon = False
        else:
            logger.info("Dragon not available - using in-memory dictionary storage")
            self.ddict = {}
            self.using_dragon = False

    def _make_key(self, namespace: Tuple[str, ...], key: str) -> str:
        """Create a hierarchical key from namespace and key"""
        return ":".join(namespace + (key,))

    def _parse_key(self, full_key: str) -> Tuple[Tuple[str, ...], str]:
        """Parse a full key back into namespace and key"""
        parts = full_key.split(":")
        if len(parts) > 0:
            return tuple(parts[:-1]), parts[-1]
        return tuple(), ""

    def get(self, namespace: Tuple[str, ...], key: str) -> Optional[Item]:
        """
        Retrieve a single item from the store.

        Args:
            namespace: Hierarchical namespace tuple
            key: The key within the namespace

        Returns:
            Item object or None if not found
        """
        full_key = self._make_key(namespace, key)

        try:
            item_data = self.ddict[full_key]

            if item_data is None:
                return None

            return Item(
                value=item_data["value"],
                key=key,
                namespace=namespace,
                created_at=item_data.get("created_at"),
                updated_at=item_data.get("updated_at"),
            )
        except KeyError:
            return None

    def put(self, namespace: Tuple[str, ...], key: str, value: dict) -> None:
        """
        Store a value in the distributed dictionary.

        Args:
            namespace: Hierarchical namespace tuple
            key: The key within the namespace
            value: Dictionary value to store
        """
        full_key = self._make_key(namespace, key)

        # Check if item exists to preserve created_at
        existing = None
        try:
            existing = self.ddict[full_key]
        except KeyError:
            pass

        now = datetime.utcnow().isoformat()

        item_data = {
            "value": value,
            "created_at": existing.get("created_at", now) if existing else now,
            "updated_at": now,
        }

        self.ddict[full_key] = item_data

    def delete(self, namespace: Tuple[str, ...], key: str) -> None:
        """
        Delete an item from the store.

        Args:
            namespace: Hierarchical namespace tuple
            key: The key to delete
        """
        full_key = self._make_key(namespace, key)
        try:
            del self.ddict[full_key]
        except KeyError:
            pass

    def search(
        self,
        namespace_prefix: Tuple[str, ...],
        filter: Optional[dict] = None,
        limit: int = 10,
        offset: int = 0,
    ) -> List[Item]:
        """
        Search for items matching a namespace prefix.

        Args:
            namespace_prefix: Namespace prefix to search within
            filter: Optional filter criteria
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of matching Item objects
        """
        prefix = ":".join(namespace_prefix) + ":" if namespace_prefix else ""
        results = []

        for full_key in self.ddict.keys():
            if full_key.startswith(prefix):
                try:
                    item_data = self.ddict[full_key]

                    # Apply filter if provided
                    if filter:
                        if not self._matches_filter(item_data.get("value", {}), filter):
                            continue

                    namespace, key = self._parse_key(full_key)

                    item = Item(
                        value=item_data.get("value"),
                        key=key,
                        namespace=namespace,
                        created_at=item_data.get("created_at"),
                        updated_at=item_data.get("updated_at"),
                    )
                    results.append(item)

                except Exception:
                    continue

        return results[offset : offset + limit]

    def _matches_filter(self, value: dict, filter: dict) -> bool:
        """Check if a value matches filter criteria"""
        for key, expected in filter.items():
            if key not in value or value[key] != expected:
                return False
        return True

    def batch(self, ops: Iterable[Op]) -> List:
        """
        Execute multiple operations in batch for efficiency.

        Args:
            ops: Iterable of operations

        Returns:
            List of results corresponding to each operation
        """
        results = []

        for op in ops:
            if isinstance(op, PutOp):
                self.put(op.namespace, op.key, op.value)
                results.append(None)

            elif isinstance(op, GetOp):
                result = self.get(op.namespace, op.key)
                results.append(result)

            elif isinstance(op, DeleteOp):
                self.delete(op.namespace, op.key)
                results.append(None)

            elif isinstance(op, SearchOp):
                search_results = self.search(
                    op.namespace_prefix,
                    filter=getattr(op, "filter", None),
                    limit=getattr(op, "limit", 10),
                    offset=getattr(op, "offset", 0),
                )
                results.append(search_results)

            elif isinstance(op, ListNamespacesOp):
                # Return unique namespaces
                namespaces = set()
                prefix = ":".join(op.prefix) + ":" if op.prefix else ""
                for full_key in self.ddict.keys():
                    if full_key.startswith(prefix):
                        namespace, _ = self._parse_key(full_key)
                        namespaces.add(namespace)
                results.append(list(namespaces))
            else:
                results.append(None)

        return results

    async def abatch(self, ops: Iterable[Op]) -> List:
        """
        Async batch operations.

        Args:
            ops: Iterable of operations

        Returns:
            List of results corresponding to each operation
        """
        return self.batch(ops)

    def close(self):
        """Clean up Dragon DDict resources"""
        if self.using_dragon and hasattr(self.ddict, "destroy"):
            try:
                self.ddict.destroy()
                logger.info("Dragon DDict resources cleaned up")
            except Exception as e:
                logger.warning(f"Error cleaning up Dragon DDict: {e}")


class DragonCheckpointSaver(BaseCheckpointSaver):
    """
    Distributed checkpoint saver for LangGraph using Dragon DDict backend.
    Stores graph execution state across distributed HPC nodes.
    
    Falls back to in-memory dictionary if Dragon is not available.
    """

    def __init__(
        self,
        managers_per_node: int = 1,
        n_nodes: int = 1,
        total_mem: int = 20 * 1024 * 1024 * 1024,  # 20 GB default
        **kwargs,
    ):
        """
        Initialize Dragon DDict checkpoint saver.

        Args:
            managers_per_node: Number of managers per node
            n_nodes: Number of nodes for distribution
            total_mem: Total memory in bytes
            **kwargs: Additional DDict parameters
        """
        super().__init__()

        # Split memory between checkpoints and writes
        checkpoint_mem = int(total_mem * 0.8)
        writes_mem = int(total_mem * 0.2)

        if DRAGON_AVAILABLE and DDict is not None:
            try:
                self.checkpoints = DDict(
                    managers_per_node=managers_per_node,
                    n_nodes=n_nodes,
                    total_mem=checkpoint_mem,
                    **kwargs,
                )

                self.writes = DDict(
                    managers_per_node=managers_per_node,
                    n_nodes=n_nodes,
                    total_mem=writes_mem,
                    **kwargs,
                )
                self.using_dragon = True
                logger.info(f"Initialized DragonCheckpointSaver with {n_nodes} nodes, {total_mem / (1024**3):.2f} GB total memory")
            except Exception as e:
                logger.warning(f"Failed to initialize Dragon DDict for checkpoints: {e}. Falling back to in-memory storage.")
                self.checkpoints = {}
                self.writes = {}
                self.using_dragon = False
        else:
            logger.info("Dragon not available - using in-memory dictionary for checkpoints")
            self.checkpoints = {}
            self.writes = {}
            self.using_dragon = False

    def _make_checkpoint_key(
        self, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> str:
        """Create key for checkpoint storage"""
        return f"checkpoint:{thread_id}:{checkpoint_ns}:{checkpoint_id}"

    def _make_writes_key(
        self,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        task_id: str,
        idx: int,
    ) -> str:
        """Create key for pending writes storage"""
        return f"writes:{thread_id}:{checkpoint_ns}:{checkpoint_id}:{task_id}:{idx}"

    def _parse_checkpoint_key(
        self, key: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Parse checkpoint key back into components"""
        parts = key.split(":")
        if len(parts) >= 4 and parts[0] == "checkpoint":
            return parts[1], parts[2], parts[3]
        return None, None, None

    def get_tuple(self, config: dict) -> Optional[CheckpointTuple]:
        """
        Get a checkpoint tuple from the store.

        Args:
            config: Configuration dict with thread_id and optional checkpoint_id

        Returns:
            CheckpointTuple or None if not found
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"].get("checkpoint_id")

        if checkpoint_id:
            key = self._make_checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
            try:
                data = self.checkpoints[key]
                return CheckpointTuple(
                    config=config,
                    checkpoint=data["checkpoint"],
                    metadata=data["metadata"],
                    parent_config=data.get("parent_config"),
                    pending_writes=self._get_pending_writes(
                        thread_id, checkpoint_ns, checkpoint_id
                    ),
                )
            except KeyError:
                return None

        # Get latest checkpoint for this thread
        latest = self._get_latest_checkpoint(thread_id, checkpoint_ns)
        if latest:
            checkpoint_id, data = latest

            return CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": checkpoint_id,
                    }
                },
                checkpoint=data["checkpoint"],
                metadata=data["metadata"],
                parent_config=data.get("parent_config"),
                pending_writes=self._get_pending_writes(
                    thread_id, checkpoint_ns, checkpoint_id
                ),
            )

        return None

    def _get_latest_checkpoint(
        self, thread_id: str, checkpoint_ns: str
    ) -> Optional[Tuple[str, dict]]:
        """Get the most recent checkpoint for a thread"""
        prefix = f"checkpoint:{thread_id}:{checkpoint_ns}:"
        latest_checkpoint = None
        latest_timestamp = None

        for key in self.checkpoints.keys():
            if key.startswith(prefix):
                try:
                    data = self.checkpoints[key]
                    checkpoint_id = key.split(":")[-1]
                    timestamp = data["metadata"].get("created_at")

                    if timestamp and (
                        latest_timestamp is None or timestamp > latest_timestamp
                    ):
                        latest_timestamp = timestamp
                        latest_checkpoint = (checkpoint_id, data)
                except Exception:
                    continue

        return latest_checkpoint

    def _get_pending_writes(
        self, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> Optional[List[Tuple]]:
        """Get pending writes for a checkpoint"""
        prefix = f"writes:{thread_id}:{checkpoint_ns}:{checkpoint_id}:"
        pending_writes = []

        for key in self.writes.keys():
            if key.startswith(prefix):
                try:
                    write_data = self.writes[key]
                    pending_writes.append(write_data)
                except Exception:
                    continue

        if not pending_writes:
            return None

        pending_writes.sort(key=lambda x: x.get("idx", 0))
        return [(w["task_id"], w["channel"], w["value"]) for w in pending_writes]

    def put(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict,
    ) -> dict:
        """
        Save a checkpoint to the store.

        Args:
            config: Configuration dict with thread_id
            checkpoint: The checkpoint to save
            metadata: Metadata for the checkpoint
            new_versions: Version information for channels

        Returns:
            Updated config with checkpoint_id
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]

        parent_checkpoint_id = config["configurable"].get("checkpoint_id")
        parent_config = None
        if parent_checkpoint_id:
            parent_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": parent_checkpoint_id,
                }
            }

        if "created_at" not in metadata:
            metadata["created_at"] = datetime.utcnow().isoformat()

        checkpoint_data = {
            "checkpoint": checkpoint,
            "metadata": metadata,
            "parent_config": parent_config,
        }

        key = self._make_checkpoint_key(thread_id, checkpoint_ns, checkpoint_id)
        self.checkpoints[key] = checkpoint_data

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: dict,
        writes: Sequence[Tuple],
        task_id: str,
    ) -> None:
        """
        Store pending writes for a checkpoint.

        Args:
            config: Configuration dict with thread_id and checkpoint_id
            writes: List of (channel, value) tuples to write
            task_id: ID of the task generating these writes
        """
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        for idx, (channel, value) in enumerate(writes):
            write_data = {
                "task_id": task_id,
                "channel": channel,
                "value": value,
                "idx": idx,
            }

            key = self._make_writes_key(
                thread_id, checkpoint_ns, checkpoint_id, task_id, idx
            )
            self.writes[key] = write_data

    def list(
        self,
        config: Optional[dict] = None,
        *,
        filter: Optional[dict] = None,
        before: Optional[dict] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        """
        List checkpoints from the store.

        Args:
            config: Base configuration to filter by
            filter: Additional metadata filters
            before: List checkpoints before this config
            limit: Maximum number of checkpoints to return

        Yields:
            CheckpointTuple objects
        """
        thread_id = config["configurable"]["thread_id"] if config else None
        checkpoint_ns = (
            config["configurable"].get("checkpoint_ns", "") if config else ""
        )

        if thread_id:
            prefix = f"checkpoint:{thread_id}:{checkpoint_ns}:"
        else:
            prefix = "checkpoint:"

        checkpoints = []
        for key in self.checkpoints.keys():
            if key.startswith(prefix):
                try:
                    parsed_thread_id, parsed_ns, checkpoint_id = (
                        self._parse_checkpoint_key(key)
                    )
                    if parsed_thread_id is None:
                        continue

                    data = self.checkpoints[key]

                    if filter:
                        metadata = data["metadata"]
                        if not all(metadata.get(k) == v for k, v in filter.items()):
                            continue

                    if before:
                        before_checkpoint_id = before["configurable"].get(
                            "checkpoint_id"
                        )
                        before_key = self._make_checkpoint_key(
                            parsed_thread_id, parsed_ns, before_checkpoint_id
                        )
                        try:
                            before_data = self.checkpoints[before_key]
                            if data["metadata"].get("created_at", "") >= before_data[
                                "metadata"
                            ].get("created_at", ""):
                                continue
                        except KeyError:
                            pass

                    checkpoints.append(
                        (parsed_thread_id, parsed_ns, checkpoint_id, data)
                    )
                except Exception:
                    continue

        checkpoints.sort(
            key=lambda x: x[3]["metadata"].get("created_at", ""), reverse=True
        )

        if limit:
            checkpoints = checkpoints[:limit]

        for thread_id, checkpoint_ns, checkpoint_id, data in checkpoints:
            yield CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": checkpoint_id,
                    }
                },
                checkpoint=data["checkpoint"],
                metadata=data["metadata"],
                parent_config=data.get("parent_config"),
                pending_writes=self._get_pending_writes(
                    thread_id, checkpoint_ns, checkpoint_id
                ),
            )

    def close(self):
        """Clean up Dragon DDict resources"""
        if self.using_dragon:
            if hasattr(self.checkpoints, "destroy"):
                try:
                    self.checkpoints.destroy()
                    logger.info("Dragon DDict checkpoint resources cleaned up")
                except Exception as e:
                    logger.warning(f"Error cleaning up Dragon DDict checkpoints: {e}")
            if hasattr(self.writes, "destroy"):
                try:
                    self.writes.destroy()
                    logger.info("Dragon DDict writes resources cleaned up")
                except Exception as e:
                    logger.warning(f"Error cleaning up Dragon DDict writes: {e}")
