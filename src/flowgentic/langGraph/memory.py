"""
Memory management system for LangGraph agents.

This module provides memory management capabilities for LangGraph integrations:
- Short-term memory: Thread-scoped conversation history management
- Long-term memory: Distributed storage using Dragon DDict (when available)
- Memory configuration: Configurable memory strategies and limits
- Integration hooks: Memory-aware graph construction and execution
- Facade pattern: Simple interface following the established component pattern

Supports both local (in-memory) and distributed (Dragon DDict) memory backends.
"""

from typing import List, Dict, Any, Optional, cast, Literal
import json
from datetime import datetime
import os

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

import logging

logger = logging.getLogger(__name__)


class MemoryConfig:
	"""Configuration for memory management strategies."""

	def __init__(
		self,
		max_short_term_messages: int = 50,
		short_term_strategy: str = "trim_last",  # "trim_last", "trim_middle", "importance_based", "summarize"
		context_window_buffer: int = 10,  # Keep buffer messages in context window
		memory_update_threshold: int = 5,  # Update memory every N interactions
		summarization_batch_size: int = 10,  # Number of messages to summarize at once
		enable_summarization: bool = False,  # Whether to use LLM-based summarization
	):
		self.max_short_term_messages = max_short_term_messages
		self.short_term_strategy = short_term_strategy
		self.context_window_buffer = context_window_buffer
		self.memory_update_threshold = memory_update_threshold
		self.summarization_batch_size = summarization_batch_size
		self.enable_summarization = enable_summarization


class DistributedMemoryConfig:
	"""Configuration for distributed memory using Dragon DDict backend.

	This configuration extends basic memory management to support distributed
	storage across HPC nodes using Dragon's DDict.
	"""

	def __init__(
		self,
		mode: Literal["local", "distributed", "auto"] = "auto",
		managers_per_node: int = 1,
		n_nodes: int = 1,
		store_total_mem: int = 10 * 1024 * 1024 * 1024,  # 10 GB for store
		checkpoint_total_mem: int = 20 * 1024 * 1024 * 1024,  # 20 GB for checkpoints
		enable_store: bool = True,  # Enable distributed long-term memory store
		enable_checkpointer: bool = True,  # Enable distributed checkpoint saver
		fallback_to_local: bool = True,  # Fall back to local if Dragon unavailable
	):
		"""Initialize distributed memory configuration.

		Args:
			mode: Memory backend mode - "local" (in-memory), "distributed" (Dragon), or "auto" (detect)
			managers_per_node: Number of Dragon managers per node
			n_nodes: Number of nodes for distribution
			store_total_mem: Total memory for long-term store in bytes
			checkpoint_total_mem: Total memory for checkpoints in bytes
			enable_store: Whether to use distributed store for long-term memory
			enable_checkpointer: Whether to use distributed checkpointer
			fallback_to_local: Whether to fall back to local storage if Dragon unavailable
		"""
		self.mode = mode
		self.managers_per_node = managers_per_node
		self.n_nodes = n_nodes
		self.store_total_mem = store_total_mem
		self.checkpoint_total_mem = checkpoint_total_mem
		self.enable_store = enable_store
		self.enable_checkpointer = enable_checkpointer
		self.fallback_to_local = fallback_to_local

	def should_use_distributed(self) -> bool:
		"""Determine if distributed memory should be used based on configuration and environment.

		Returns:
			True if distributed memory should be used, False otherwise
		"""
		if self.mode == "local":
			return False
		elif self.mode == "distributed":
			return True
		else:  # auto mode
			# Check for HPC environment indicators
			hpc_indicators = [
				"SLURM_JOB_ID",
				"PBS_JOBID",
				"LSB_JOBID",
				"DRAGON_RUNTIME",
			]
			return any(os.getenv(indicator) for indicator in hpc_indicators)


class ShortTermMemoryItem(BaseModel):
	"""Represents a memory item in short-term storage."""

	content: str
	message_type: str
	timestamp: datetime
	importance: float = Field(default=1.0)

	def to_dict(self) -> Dict[str, Any]:
		return self.model_dump()

	@classmethod
	def from_message(cls, message: BaseMessage) -> "ShortTermMemoryItem":
		"""Create a ShortTermMemoryItem from a BaseMessage."""
		content = (
			message.content
			if isinstance(message.content, str)
			else str(message.content)
		)
		message_type = type(message).__name__
		importance = cls._calculate_importance(message)

		return cls(
			content=content,
			message_type=message_type,
			timestamp=datetime.now(),
			importance=importance,
		)

	@staticmethod
	def _calculate_importance(message: BaseMessage) -> float:
		"""Calculate importance score for a message."""
		base_importance = 1.0

		# System messages are more important
		if isinstance(message, SystemMessage):
			base_importance = 2.0
		# AI messages with tool calls are more important
		elif (
			isinstance(message, AIMessage)
			and hasattr(message, "tool_calls")
			and message.tool_calls
		):
			base_importance = 1.5
		# Human messages are important for context
		elif isinstance(message, HumanMessage):
			base_importance = 1.2

		# Increase importance for longer messages (potential for more information)
		content_length = (
			len(message.content)
			if isinstance(message.content, str)
			else len(str(message.content))
		)
		length_bonus = min(content_length / 1000.0, 1.0)  # Cap at 1.0 bonus

		return base_importance + length_bonus


class ShortTermMemoryManager:
	"""Manages short-term memory within conversation threads."""

	def __init__(self, config: MemoryConfig, llm: Optional[BaseChatModel] = None):
		self.config = config
		self.llm = llm
		self.message_history: List[BaseMessage] = []
		self.memory_items: List[ShortTermMemoryItem] = []
		self.interaction_count = 0

	def add_messages(self, messages: List[BaseMessage]) -> List[BaseMessage]:
		"""Add messages to short-term memory and apply trimming strategy."""
		self.message_history.extend(messages)
		# Create memory items for importance tracking
		for msg in messages:
			self.memory_items.append(ShortTermMemoryItem.from_message(msg))
		self.interaction_count += 1

		# Apply trimming strategy if needed
		if len(self.message_history) > self.config.max_short_term_messages:
			self.message_history = self._apply_trimming_strategy()

		return self.message_history.copy()

	def _apply_trimming_strategy(self) -> List[BaseMessage]:
		"""Apply the configured trimming strategy."""
		if self.config.short_term_strategy == "trim_last":
			return self._trim_from_end()
		elif self.config.short_term_strategy == "trim_middle":
			return self._trim_from_middle()
		elif self.config.short_term_strategy == "importance_based":
			return self._trim_by_importance()
		elif (
			self.config.short_term_strategy == "summarize"
			and self.llm
			and self.config.enable_summarization
		):
			return self._summarize_old_messages()
		else:
			return self._trim_from_end()  # Default fallback

	def _trim_from_end(self) -> List[BaseMessage]:
		"""Keep most recent messages, prioritizing system messages."""
		if len(self.message_history) <= self.config.max_short_term_messages:
			return self.message_history

		# Always keep system messages
		system_msgs = [m for m in self.message_history if isinstance(m, SystemMessage)]
		other_msgs = [
			m for m in self.message_history if not isinstance(m, SystemMessage)
		]

		# Keep the most recent messages
		keep_count = self.config.max_short_term_messages - len(system_msgs)
		trimmed_other = other_msgs[-keep_count:] if keep_count > 0 else []

		return system_msgs + trimmed_other

	def _trim_from_middle(self) -> List[BaseMessage]:
		"""Remove messages from the middle, keeping beginning and end."""
		if len(self.message_history) <= self.config.max_short_term_messages:
			return self.message_history

		system_msgs = [m for m in self.message_history if isinstance(m, SystemMessage)]
		other_msgs = [
			m for m in self.message_history if not isinstance(m, SystemMessage)
		]

		keep_count = self.config.max_short_term_messages - len(system_msgs)
		if keep_count <= 0:
			return cast(List[BaseMessage], system_msgs)

		# Keep messages from beginning and end
		half_keep = keep_count // 2
		beginning = other_msgs[:half_keep]
		end = other_msgs[-half_keep:]

		return system_msgs + beginning + end

	def _trim_by_importance(self) -> List[BaseMessage]:
		"""Trim messages based on importance scores, keeping most important ones."""
		if len(self.message_history) <= self.config.max_short_term_messages:
			return self.message_history

		# Always keep system messages
		system_msgs = [m for m in self.message_history if isinstance(m, SystemMessage)]
		other_messages_with_importance = [
			(msg, self.memory_items[i].importance)
			for i, msg in enumerate(self.message_history)
			if not isinstance(msg, SystemMessage)
		]

		# Sort by importance (highest first)
		other_messages_with_importance.sort(key=lambda x: x[1], reverse=True)

		# Keep the most important messages
		keep_count = self.config.max_short_term_messages - len(system_msgs)
		if keep_count > 0:
			kept_other = [msg for msg, _ in other_messages_with_importance[:keep_count]]
		else:
			kept_other = []

		return system_msgs + kept_other

	def _summarize_old_messages(self) -> List[BaseMessage]:
		"""Summarize old messages using LLM to reduce memory usage while preserving information."""
		if not self.llm or not self.config.enable_summarization:
			return self._trim_from_end()  # Fallback if summarization not available

		if len(self.message_history) <= self.config.max_short_term_messages:
			return self.message_history

		# Always keep system messages
		system_msgs = cast(
			List[BaseMessage],
			[m for m in self.message_history if isinstance(m, SystemMessage)],
		)
		other_msgs = [
			m for m in self.message_history if not isinstance(m, SystemMessage)
		]

		# Calculate how many messages to keep unsummarized (most recent)
		keep_total = self.config.max_short_term_messages - len(system_msgs)
		keep_unsummarized = min(
			max(2, keep_total // 2), keep_total
		)  # Keep at least half for recent context, but not more than total allowed

		if keep_total <= 0:
			return cast(List[BaseMessage], system_msgs)

		# Keep most recent messages unsummarized
		recent_msgs = (
			other_msgs[-keep_unsummarized:]
			if keep_unsummarized < len(other_msgs)
			else other_msgs
		)

		# Messages to summarize
		messages_to_summarize = (
			other_msgs[:-keep_unsummarized]
			if keep_unsummarized < len(other_msgs)
			else []
		)

		if not messages_to_summarize:
			return system_msgs + recent_msgs

		# Create summary of old messages
		try:
			summary_message = self._create_conversation_summary(messages_to_summarize)
			summarized_msgs = [summary_message] if summary_message else []
		except Exception:
			# If summarization fails, fall back to trimming
			summarized_msgs = messages_to_summarize[: keep_total - len(recent_msgs)]

		return system_msgs + summarized_msgs + recent_msgs

	def _create_conversation_summary(
		self, messages: List[BaseMessage]
	) -> Optional[BaseMessage]:
		"""Use LLM to create a summary of the given messages."""
		if not messages or self.llm is None:
			return None

		# Prepare conversation text for summarization
		conversation_text = self._format_messages_for_summary(messages)

		# Create summarization prompt
		summary_prompt = f"""Please provide a concise summary of the following conversation excerpt.
Focus on key information, decisions, and important context that would be relevant for continuing the conversation:

{conversation_text}

Summary:"""

		try:
			# Use LLM to generate summary
			response = self.llm.invoke([HumanMessage(content=summary_prompt)])

			if hasattr(response, "content") and response.content:
				content_str = (
					str(response.content)
					if not isinstance(response.content, str)
					else response.content
				)
				summary_content = (
					f"Previous conversation summary: {content_str.strip()}"
				)
				return AIMessage(content=summary_content)
		except Exception:
			logger.debug(f"LLM invokation failed")

		return None

	def _format_messages_for_summary(self, messages: List[BaseMessage]) -> str:
		"""Format messages into a readable conversation format for summarization."""
		formatted_parts = []

		for msg in messages:
			if isinstance(msg, HumanMessage):
				formatted_parts.append(f"User: {msg.content}")
			elif isinstance(msg, AIMessage):
				formatted_parts.append(f"Assistant: {msg.content}")
			elif isinstance(msg, SystemMessage):
				formatted_parts.append(f"System: {msg.content}")
			else:
				formatted_parts.append(f"Other: {msg.content}")

		return "\n".join(formatted_parts)

	def get_recent_messages(self, count: Optional[int] = None) -> List[BaseMessage]:
		"""Get the most recent messages."""
		if count is None:
			return self.message_history.copy()
		return self.message_history[-count:].copy()

	def get_memory_stats(self) -> Dict[str, Any]:
		"""Get statistics about current memory state."""
		return {
			"total_messages": len(self.message_history),
			"interaction_count": self.interaction_count,
			"system_messages": len(
				[m for m in self.message_history if isinstance(m, SystemMessage)]
			),
			"human_messages": len(
				[m for m in self.message_history if isinstance(m, HumanMessage)]
			),
			"ai_messages": len(
				[m for m in self.message_history if isinstance(m, AIMessage)]
			),
		}

	def clear(self):
		"""Clear all short-term memory."""
		self.message_history.clear()
		self.memory_items.clear()
		self.interaction_count = 0

	def consolidate_memory(self) -> Dict[str, Any]:
		"""Consolidate and optimize memory storage."""
		# Remove any inconsistencies between message_history and memory_items
		if len(self.message_history) != len(self.memory_items):
			# Rebuild memory_items if there's a mismatch
			self.memory_items = [
				ShortTermMemoryItem.from_message(msg) for msg in self.message_history
			]

		# Update importance scores based on recent interactions
		self._update_importance_scores()

		# Clean up old or low-importance items if memory is getting full
		self._cleanup_low_importance_items()

		return {
			"consolidated_messages": len(self.message_history),
			"memory_items": len(self.memory_items),
			"avg_importance": sum(item.importance for item in self.memory_items)
			/ len(self.memory_items)
			if self.memory_items
			else 0.0,
		}

	def _update_importance_scores(self):
		"""Update importance scores based on recency and content."""
		if not self.memory_items:
			return

		current_time = datetime.now()
		for item in self.memory_items:
			# Decay importance based on age (newer messages are more important)
			age_hours = (current_time - item.timestamp).total_seconds() / 3600
			recency_bonus = max(0.5, 1.0 - age_hours / 24.0)  # Decay over 24 hours

			# Update importance
			item.importance = item.importance * recency_bonus

	def _cleanup_low_importance_items(self):
		"""Remove items with very low importance if memory is full."""
		if len(self.message_history) <= self.config.max_short_term_messages:
			return

		# Keep system messages and high-importance messages
		min_importance_threshold = 0.5

		# Get indices to keep
		keep_indices = []
		for i, (msg, item) in enumerate(zip(self.message_history, self.memory_items)):
			if (
				isinstance(msg, SystemMessage)
				or item.importance >= min_importance_threshold
			):
				keep_indices.append(i)

		# If we still have too many, keep the most recent ones
		if len(keep_indices) > self.config.max_short_term_messages:
			keep_indices = keep_indices[-self.config.max_short_term_messages :]

		# Filter both lists
		self.message_history = [self.message_history[i] for i in keep_indices]
		self.memory_items = [self.memory_items[i] for i in keep_indices]


class MemoryManager:
	"""Main memory management interface for LangGraph integration."""

	def __init__(self, config: MemoryConfig, llm: Optional[BaseChatModel] = None):
		self.config = config
		self.short_term_manager = ShortTermMemoryManager(config, llm)

	async def add_interaction(
		self,
		user_id: str,
		messages: List[BaseMessage],
		metadata: Optional[Dict[str, Any]] = None,
	) -> Dict[str, Any]:
		"""Add an interaction to memory and track statistics."""
		# Add to short-term memory
		current_messages = self.short_term_manager.add_messages(messages)

		return {
			"short_term_messages": len(current_messages),
			"total_interactions": self.short_term_manager.interaction_count,
			"memory_stats": self.short_term_manager.get_memory_stats(),
		}

	async def get_relevant_context(
		self, user_id: str, query: Optional[str] = None
	) -> Dict[str, Any]:
		"""Get relevant context from memory with smart retrieval."""
		# Get short-term memory
		short_term_messages = self.short_term_manager.get_recent_messages()

		# For now, return basic context. This can be enhanced with semantic search
		# when long-term memory is implemented
		context = {
			"short_term": short_term_messages,
			"memory_stats": self.short_term_manager.get_memory_stats(),
			"relevant_messages": self._get_relevant_messages(
				short_term_messages, query
			),
		}

		return context

	def _get_relevant_messages(
		self, messages: List[BaseMessage], query: Optional[str] = None
	) -> List[BaseMessage]:
		"""Extract relevant messages based on semantic search and importance."""
		if not messages:
			# Return empty list if no messages
			return []

		if not query:
			# Return recent messages if no query
			return messages[-10:] if len(messages) > 10 else messages

		# Perform semantic search with multiple strategies
		scored_messages = []
		messages_count = len(messages)

		for i, msg in enumerate(messages):
			if not hasattr(msg, "content") or not isinstance(msg.content, str):
				continue

			content = msg.content
			# Skip messages with empty content
			if not content or not content.strip():
				continue

			score = self._calculate_semantic_relevance(content, query)

			# Boost score based on importance and recency
			importance_boost = (
				self.short_term_manager.memory_items[i].importance
				if i < len(self.short_term_manager.memory_items)
				else 1.0
			)
			# Protect against division by zero
			recency_boost = (
				max(0.5, 1.0 - (messages_count - i) / messages_count)
				if messages_count > 0
				else 1.0
			)  # More recent = higher boost

			total_score = score * importance_boost * recency_boost
			scored_messages.append((total_score, msg))

		# Sort by relevance score (highest first) and return top matches
		scored_messages.sort(key=lambda x: x[0], reverse=True)

		# Return up to 8 most relevant messages, ensuring we have at least some recent context
		relevant = [msg for score, msg in scored_messages[:8]]

		# Always include some recent messages for context
		recent_context = messages[-3:] if len(messages) > 3 else messages
		for msg in recent_context:
			if msg not in relevant:
				relevant.append(msg)

		return relevant[:10]  # Cap at 10 messages

	def _calculate_semantic_relevance(self, content: str, query: str) -> float:
		"""Calculate semantic relevance score between content and query."""
		# Handle empty content
		if not content or not query:
			return 0.0

		content_lower = content.lower()
		query_lower = query.lower()

		# Exact phrase matching (highest weight)
		if query_lower in content_lower:
			return 1.0

		# Word overlap scoring
		query_words = set(query_lower.split())
		content_words = set(content_lower.split())

		# Jaccard similarity
		intersection = len(query_words & content_words)
		union = len(query_words | content_words)

		if union == 0:
			return 0.0

		jaccard_score = intersection / union

		# Boost for consecutive word matches (n-grams)
		consecutive_boost = 0.0
		query_tokens = query_lower.split()
		content_tokens = content_lower.split()

		for i in range(len(query_tokens)):
			for j in range(len(content_tokens) - i):
				if (
					query_tokens[i : i + 2] == content_tokens[j : j + 2]
				):  # Bigram matching
					consecutive_boost += 0.2
				elif (
					i < len(query_tokens) - 2
					and query_tokens[i : i + 3] == content_tokens[j : j + 3]
				):  # Trigram matching
					consecutive_boost += 0.3

		# Length normalization (shorter matches get slight boost)
		# Protect against division by zero
		length_factor = min(1.0, 50.0 / len(content)) if len(content) > 0 else 0.0

		return min(1.0, jaccard_score + consecutive_boost + length_factor * 0.1)

	def get_short_term_messages(self) -> List[BaseMessage]:
		"""Get current short-term messages."""
		return self.short_term_manager.get_recent_messages()

	def clear_short_term_memory(self):
		"""Clear short-term memory."""
		self.short_term_manager.clear()

	async def consolidate_memory(self) -> Dict[str, Any]:
		"""Consolidate and optimize all memory systems."""
		# Consolidate short-term memory
		short_term_stats = self.short_term_manager.consolidate_memory()

		# For now, long-term memory consolidation will be added when implemented
		return {"short_term": short_term_stats, "timestamp": datetime.now().isoformat()}

	def get_memory_health(self) -> Dict[str, Any]:
		"""Get overall memory system health and statistics."""
		stats = self.short_term_manager.get_memory_stats()
		stats.update(
			{
				"config": {
					"max_short_term_messages": self.config.max_short_term_messages,
					"short_term_strategy": self.config.short_term_strategy,
					"context_window_buffer": self.config.context_window_buffer,
					"memory_update_threshold": self.config.memory_update_threshold,
				},
				"memory_efficiency": len(self.short_term_manager.message_history)
				/ self.config.max_short_term_messages
				if self.config.max_short_term_messages > 0
				else 1.0,
				"average_importance": sum(
					item.importance for item in self.short_term_manager.memory_items
				)
				/ len(self.short_term_manager.memory_items)
				if self.short_term_manager.memory_items
				else 0.0,
			}
		)
		return stats


# State definitions for memory-enabled graphs
class MemoryEnabledState(BaseModel):
	"""State schema for graphs with memory support."""

	messages: List[BaseMessage]
	user_id: str
	memory_context: Dict[str, Any]


class DistributedMemoryManager:
	"""Manager for distributed memory using Dragon DDict backend.

	Provides a unified interface for distributed long-term memory storage
	and checkpoint persistence across HPC nodes. Automatically falls back
	to local storage if Dragon is not available.
	"""

	def __init__(
		self,
		config: Optional[DistributedMemoryConfig] = None,
	):
		"""Initialize distributed memory manager.

		Args:
			config: Distributed memory configuration. If None, uses defaults with auto-detection.
		"""
		self.config = config or DistributedMemoryConfig()
		self._store = None
		self._checkpointer = None
		self._initialized = False

		logger.info(
			f"Initialized DistributedMemoryManager with mode: {self.config.mode}"
		)

	def _initialize_backends(self):
		"""Lazy initialization of distributed memory backends."""
		if self._initialized:
			return

		use_distributed = self.config.should_use_distributed()

		if use_distributed:
			logger.info("Attempting to initialize Dragon DDict backends")
			try:
				from flowgentic.langGraph.distributed_memory import (
					DragonDDictStore,
					DragonCheckpointSaver,
				)

				if self.config.enable_store:
					self._store = DragonDDictStore(
						managers_per_node=self.config.managers_per_node,
						n_nodes=self.config.n_nodes,
						total_mem=self.config.store_total_mem,
					)
					logger.info("Initialized DragonDDictStore for long-term memory")

				if self.config.enable_checkpointer:
					self._checkpointer = DragonCheckpointSaver(
						managers_per_node=self.config.managers_per_node,
						n_nodes=self.config.n_nodes,
						total_mem=self.config.checkpoint_total_mem,
					)
					logger.info("Initialized DragonCheckpointSaver for checkpoints")

			except Exception as e:
				logger.warning(f"Failed to initialize Dragon backends: {e}")
				if self.config.fallback_to_local:
					logger.info("Falling back to local memory storage")
					self._store = None
					self._checkpointer = None
				else:
					raise
		else:
			logger.info("Using local memory storage (distributed mode not enabled)")

		self._initialized = True

	def get_store(self):
		"""Get the distributed store backend.

		Returns:
			DragonDDictStore instance or None if not available/enabled
		"""
		self._initialize_backends()
		return self._store

	def get_checkpointer(self):
		"""Get the distributed checkpoint saver backend.

		Returns:
			DragonCheckpointSaver instance or None if not available/enabled
		"""
		self._initialize_backends()
		return self._checkpointer

	def is_distributed(self) -> bool:
		"""Check if distributed memory is actually being used.

		Returns:
			True if using distributed backends, False if using local
		"""
		self._initialize_backends()
		return self._store is not None or self._checkpointer is not None

	def get_memory_info(self) -> Dict[str, Any]:
		"""Get information about the current memory configuration.

		Returns:
			Dictionary with memory backend information
		"""
		self._initialize_backends()

		return {
			"mode": self.config.mode,
			"is_distributed": self.is_distributed(),
			"store_enabled": self._store is not None,
			"checkpointer_enabled": self._checkpointer is not None,
			"n_nodes": self.config.n_nodes,
			"managers_per_node": self.config.managers_per_node,
			"store_memory_gb": self.config.store_total_mem / (1024**3),
			"checkpoint_memory_gb": self.config.checkpoint_total_mem / (1024**3),
		}

	def close(self):
		"""Clean up distributed memory resources."""
		if self._store and hasattr(self._store, "close"):
			try:
				self._store.close()
				logger.info("Closed distributed store")
			except Exception as e:
				logger.warning(f"Error closing distributed store: {e}")

		if self._checkpointer and hasattr(self._checkpointer, "close"):
			try:
				self._checkpointer.close()
				logger.info("Closed distributed checkpointer")
			except Exception as e:
				logger.warning(f"Error closing distributed checkpointer: {e}")


# Facade class following the established pattern for LangGraph integration
class LangraphMemoryManager:
	"""Facade for memory management in LangGraph integration.

	Follows the same pattern as LangraphToolFaultTolerance and AgentLogger,
	providing a simple interface for memory operations within the LangraphIntegration.

	Supports both local (short-term) and distributed (long-term) memory backends.
	"""

	def __init__(
		self,
		config: Optional[MemoryConfig] = None,
		llm: Optional[BaseChatModel] = None,
		distributed_config: Optional[DistributedMemoryConfig] = None,
	) -> None:
		"""Initialize the memory manager facade.

		Args:
		    config: Memory configuration for short-term memory. If None, uses default configuration.
		    llm: Language model for summarization features. Optional.
		    distributed_config: Configuration for distributed memory. If None, uses defaults.
		"""
		self.config = config or MemoryConfig()
		self.llm = llm
		self.distributed_config = distributed_config or DistributedMemoryConfig()
		self._memory_manager: Optional[MemoryManager] = None
		self._distributed_manager: Optional[DistributedMemoryManager] = None

		logger.info(
			f"Initialized LangraphMemoryManager with strategy: {self.config.short_term_strategy}, "
			f"distributed mode: {self.distributed_config.mode}"
		)

	def _get_memory_manager(self) -> MemoryManager:
		"""Lazy initialization of the underlying MemoryManager."""
		if self._memory_manager is None:
			self._memory_manager = MemoryManager(self.config, self.llm)
			logger.debug("Created underlying MemoryManager instance")
		return self._memory_manager

	def _get_distributed_manager(self) -> DistributedMemoryManager:
		"""Lazy initialization of the distributed memory manager."""
		if self._distributed_manager is None:
			self._distributed_manager = DistributedMemoryManager(
				self.distributed_config
			)
			logger.debug("Created underlying DistributedMemoryManager instance")
		return self._distributed_manager

	async def add_interaction(
		self,
		user_id: str,
		messages: List[BaseMessage],
		metadata: Optional[Dict[str, Any]] = None,
	) -> Dict[str, Any]:
		"""Add an interaction to memory and track statistics.

		Args:
		    user_id: Identifier for the user/conversation thread
		    messages: List of messages to add to memory
		    metadata: Optional metadata for the interaction

		Returns:
		    Dictionary with memory statistics and operation results
		"""
		logger.debug(
			f"Adding interaction for user '{user_id}' with {len(messages)} messages"
		)
		memory_manager = self._get_memory_manager()
		return await memory_manager.add_interaction(user_id, messages, metadata)

	async def get_relevant_context(
		self, user_id: str, query: Optional[str] = None
	) -> Dict[str, Any]:
		"""Get relevant context from memory with smart retrieval.

		Args:
		    user_id: Identifier for the user/conversation thread
		    query: Optional query string for semantic search

		Returns:
		    Dictionary containing relevant context and memory statistics
		"""
		logger.debug(f"Getting relevant context for user '{user_id}'")
		memory_manager = self._get_memory_manager()
		return await memory_manager.get_relevant_context(user_id, query)

	def get_short_term_messages(self) -> List[BaseMessage]:
		"""Get current short-term messages.

		Returns:
		    List of current messages in short-term memory
		"""
		memory_manager = self._get_memory_manager()
		return memory_manager.get_short_term_messages()

	def clear_short_term_memory(self) -> None:
		"""Clear short-term memory."""
		logger.debug("Clearing short-term memory")
		memory_manager = self._get_memory_manager()
		memory_manager.clear_short_term_memory()

	async def consolidate_memory(self) -> Dict[str, Any]:
		"""Consolidate and optimize all memory systems.

		Returns:
		    Dictionary with consolidation results and statistics
		"""
		logger.debug("Consolidating memory")
		memory_manager = self._get_memory_manager()
		return await memory_manager.consolidate_memory()

	def get_memory_health(self) -> Dict[str, Any]:
		"""Get overall memory system health and statistics.

		Returns:
		    Dictionary with memory health metrics and configuration
		"""
		memory_manager = self._get_memory_manager()
		return memory_manager.get_memory_health()

	def update_config(self, config: MemoryConfig) -> None:
		"""Update memory configuration.

		Args:
		    config: New memory configuration to apply
		"""
		logger.info(
			f"Updating memory configuration to strategy: {config.short_term_strategy}"
		)
		self.config = config
		# Force recreation of memory manager with new config
		if self._memory_manager is not None:
			self._memory_manager = MemoryManager(self.config, self.llm)

	def get_store(self):
		"""Get the distributed store backend for long-term memory.

		Returns:
			DragonDDictStore instance or None if not available/enabled
		"""
		distributed_manager = self._get_distributed_manager()
		return distributed_manager.get_store()

	def get_checkpointer(self):
		"""Get the distributed checkpoint saver backend.

		Returns:
			DragonCheckpointSaver instance or None if not available/enabled
		"""
		distributed_manager = self._get_distributed_manager()
		return distributed_manager.get_checkpointer()

	def is_distributed(self) -> bool:
		"""Check if distributed memory is being used.

		Returns:
			True if using distributed backends, False if using local only
		"""
		distributed_manager = self._get_distributed_manager()
		return distributed_manager.is_distributed()

	def get_full_memory_info(self) -> Dict[str, Any]:
		"""Get comprehensive information about memory configuration.

		Returns:
			Dictionary with both short-term and distributed memory information
		"""
		memory_health = self.get_memory_health()
		distributed_manager = self._get_distributed_manager()
		distributed_info = distributed_manager.get_memory_info()

		return {
			"short_term": memory_health,
			"distributed": distributed_info,
		}

	def close(self) -> None:
		"""Clean up all memory resources."""
		if self._distributed_manager is not None:
			self._distributed_manager.close()
			logger.info("Closed distributed memory manager")
