"""
Memory Summarization Example

This example demonstrates the LLM-powered memory summarization and compaction
features of FlowGentic. It shows how to:

1. Enable summarization with LLM integration
2. Configure summarization parameters
3. Monitor summarization effectiveness
4. Compare summarized vs non-summarized memory

FEATURES:
    [X] LLM-powered summarization
    [X] Automatic memory compaction
    [X] Summarization configuration
    [X] Fallback handling
    [ ] Real LLM integration (requires API keys)
"""

import asyncio
from typing import List, cast
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import ChatResult, ChatGeneration

# Import our memory components
from flowgentic.langGraph.memory import (
	MemoryManager,
	MemoryConfig,
	LangraphMemoryManager,
)


class MockSummarizationLLM(BaseChatModel):
	"""Mock LLM for demonstrating summarization without requiring real API calls."""

	model_name: str = "mock-summarizer"

	@property
	def _llm_type(self) -> str:
		return "mock-summarizer"

	def _generate(self, messages, stop=None, run_manager=None, **kwargs):
		# Check if this is a summarization request
		if messages and "conversation excerpt" in messages[0].content:
			summary = "Summary: User discussed food preferences, asked about weather, and inquired about recipes. Key topics: pizza, pasta, Italian cuisine, weather information."
		else:
			summary = "Mock response"
		return ChatResult(
			generations=[
				ChatGeneration(text=summary, message=AIMessage(content=summary))
			],
			llm_output={},
		)

	async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
		return self._generate(messages, stop, run_manager, **kwargs)


async def demonstrate_summarization():
	"""Demonstrate memory summarization capabilities."""

	print("=" * 70)
	print("FlowGentic Memory Summarization Example")
	print("=" * 70)

	# Setup mock LLM for summarization
	summarization_llm = MockSummarizationLLM()

	# Example 1: Basic summarization setup
	print("\n1. Basic Summarization Configuration")
	print("-" * 40)

	memory_config = MemoryConfig(
		max_short_term_messages=6,
		short_term_strategy="summarize",
		enable_summarization=True,
		summarization_batch_size=3,
	)

	memory_manager = LangraphMemoryManager(memory_config, summarization_llm)

	print(f"Strategy: {memory_config.short_term_strategy}")
	print(f"Summarization enabled: {memory_config.enable_summarization}")
	print(f"Max messages: {memory_config.max_short_term_messages}")
	print(f"Batch size: {memory_config.summarization_batch_size}")

	# Example 2: Build up a conversation that triggers summarization
	print("\n2. Conversation Building and Summarization Trigger")
	print("-" * 50)

	# Add initial conversation
	initial_messages = cast(
		List[BaseMessage],
		[
			SystemMessage(
				content="You are a helpful assistant specializing in food and weather."
			),
			HumanMessage(content="I love pizza and Italian food in general."),
			AIMessage(
				content="That's great! Pizza is delicious. What kind of Italian food do you enjoy most?"
			),
			HumanMessage(content="Pasta dishes are my favorite too."),
			AIMessage(content="Pasta is wonderful! There are so many varieties."),
		],
	)

	print("Adding initial conversation...")
	for i, msg in enumerate(initial_messages):
		await memory_manager.add_interaction("user_food", [msg])
		stats = memory_manager.get_memory_health()
		print(f"After message {i + 1}: {stats['total_messages']} messages in memory")

	# Add more messages to trigger summarization
	additional_messages = cast(
		List[BaseMessage],
		[
			HumanMessage(content="What's the weather like in Rome today?"),
			AIMessage(
				content="I don't have access to real-time weather data, but Rome is typically sunny in summer."
			),
			HumanMessage(content="Can you tell me about some good pasta recipes?"),
			AIMessage(
				content="Sure! Carbonara is a classic Roman pasta dish with eggs, cheese, and pancetta."
			),
			HumanMessage(
				content="That sounds amazing! Do you have any pizza recommendations?"
			),
			AIMessage(
				content="For pizza, I'd recommend a Margherita with fresh mozzarella and basil."
			),
			HumanMessage(content="Perfect! Thanks for the food suggestions."),
		],
	)

	print("\nAdding more messages to trigger summarization...")
	for i, msg in enumerate(additional_messages):
		await memory_manager.add_interaction("user_food", [msg])
		stats = memory_manager.get_memory_health()
		print(f"After additional message {i + 1}: {stats['total_messages']} messages")

		# Check if summarization was triggered
		if stats["total_messages"] <= memory_config.max_short_term_messages:
			print("  -> Summarization may have occurred!")

	# Example 3: Examine final memory state
	print("\n3. Final Memory State Analysis")
	print("-" * 35)

	final_stats = memory_manager.get_memory_health()
	final_messages = memory_manager.get_short_term_messages()

	print(f"Final memory contains: {final_stats['total_messages']} messages")
	print("Message types in final memory:")

	for i, msg in enumerate(final_messages):
		msg_type = type(msg).__name__
		content = str(msg.content) if msg.content else ""
		content_preview = content[:60] + "..." if len(content) > 60 else content
		print(f"  {i + 1}. {msg_type}: {content_preview}")

	# Example 4: Test context retrieval with summarization
	print("\n4. Context Retrieval with Summarized Memory")
	print("-" * 45)

	test_queries = ["pizza", "pasta", "weather", "food preferences"]

	for query in test_queries:
		context = await memory_manager.get_relevant_context("user_food", query)
		relevant_count = len(context.get("relevant_messages", []))
		short_term_count = len(context.get("short_term", []))
		print(
			f"Query '{query}': {relevant_count} relevant, {short_term_count} total in context"
		)

	# Example 5: Memory consolidation
	print("\n5. Memory Consolidation")
	print("-" * 25)

	consolidation_result = await memory_manager.consolidate_memory()
	print(f"Consolidation result: {consolidation_result}")

	print("\n" + "=" * 70)
	print("Summarization Benefits:")
	print("=" * 70)
	print("✓ Reduces token usage while preserving key information")
	print("✓ Maintains conversation continuity")
	print("✓ Allows longer conversation threads")
	print("✓ Automatic fallback to trimming if summarization fails")
	print("✓ Configurable batch sizes and thresholds")


async def compare_with_without_summarization():
	"""Compare memory behavior with and without summarization."""

	print("\n" + "=" * 70)
	print("Comparison: With vs Without Summarization")
	print("=" * 70)

	# Setup identical configurations except for summarization
	base_config = MemoryConfig(max_short_term_messages=5)

	# Without summarization
	config_no_summary = MemoryConfig(
		**base_config.__dict__, short_term_strategy="trim_last"
	)

	# With summarization
	config_with_summary = MemoryConfig(
		**base_config.__dict__,
		short_term_strategy="summarize",
		enable_summarization=True,
	)

	# Create managers
	manager_no_summary = LangraphMemoryManager(config_no_summary)
	manager_with_summary = LangraphMemoryManager(
		config_with_summary, MockSummarizationLLM()
	)

	# Add the same conversation to both
	test_conversation = cast(
		List[BaseMessage],
		[
			SystemMessage(content="You are a helpful assistant."),
			HumanMessage(content="Hello!"),
			AIMessage(content="Hi there! How can I help you?"),
			HumanMessage(content="Tell me about machine learning."),
			AIMessage(content="Machine learning is a subset of AI..."),
			HumanMessage(content="What about deep learning?"),
			AIMessage(content="Deep learning uses neural networks..."),
			HumanMessage(content="Give me an example application."),
			AIMessage(content="One example is image recognition..."),
		],
	)

	print("Adding identical conversation to both memory managers...")

	for i, msg in enumerate(test_conversation):
		await manager_no_summary.add_interaction("user_compare", [msg])
		await manager_with_summary.add_interaction("user_compare", [msg])

		if (i + 1) % 3 == 0:
			stats_no_sum = manager_no_summary.get_memory_health()
			stats_with_sum = manager_with_summary.get_memory_health()
			print(f"After {i + 1} messages:")
			print(f"  No summarization: {stats_no_sum['total_messages']} messages")
			print(f"  With summarization: {stats_with_sum['total_messages']} messages")

	print("\nFinal comparison:")
	final_no_sum = manager_no_summary.get_memory_health()
	final_with_sum = manager_with_summary.get_memory_health()

	print(f"No summarization: {final_no_sum['total_messages']} messages")
	print(f"With summarization: {final_with_sum['total_messages']} messages")
	print(f"Information preserved: Summarization maintains context in fewer messages")


async def demonstrate_summarization_fallback():
	"""Demonstrate fallback behavior when summarization fails."""

	print("\n" + "=" * 70)
	print("Summarization Fallback Demonstration")
	print("=" * 70)

	# Create a failing LLM mock
	class FailingLLM(BaseChatModel):
		model_name: str = "failing-llm"

		@property
		def _llm_type(self) -> str:
			return "failing-llm"

		def _generate(self, messages, stop=None, run_manager=None, **kwargs):
			raise Exception("LLM service unavailable")

		async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
			raise Exception("LLM service unavailable")

	failing_llm = FailingLLM()

	memory_config = MemoryConfig(
		max_short_term_messages=4,
		short_term_strategy="summarize",
		enable_summarization=True,
	)

	memory_manager = LangraphMemoryManager(memory_config, failing_llm)

	print("Adding messages with failing LLM (should fallback to trimming)...")

	for i in range(8):
		messages = cast(List[BaseMessage], [HumanMessage(content=f"Message {i + 1}")])
		await memory_manager.add_interaction("user_fallback", messages)

		stats = memory_manager.get_memory_health()
		print(f"After message {i + 1}: {stats['total_messages']} messages")

	final_stats = memory_manager.get_memory_health()
	print(f"\nFinal result: {final_stats['total_messages']} messages")
	print("Fallback to trimming worked successfully despite LLM failures!")


if __name__ == "__main__":
	import sys

	if len(sys.argv) > 1:
		if sys.argv[1] == "--compare":
			asyncio.run(compare_with_without_summarization())
		elif sys.argv[1] == "--fallback":
			asyncio.run(demonstrate_summarization_fallback())
		else:
			print("Usage: python 04-memory-summarization.py [--compare|--fallback]")
	else:
		asyncio.run(demonstrate_summarization())
