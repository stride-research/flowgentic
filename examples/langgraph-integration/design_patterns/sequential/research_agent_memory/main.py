"""
Memory-Enabled Sequential Workflow Example

This example demonstrates how to integrate FlowGentic's memory management
system with a sequential (pipeline) workflow pattern. The workflow maintains
context across multiple stages using intelligent memory strategies.

Key Features:
- Memory-aware preprocessing, research, and synthesis stages
- Importance-based memory trimming
- Context retrieval for each stage
- Memory health monitoring and statistics

Run this example:
    python -m examples.langgraph-integration.design_patterns.sequential.research_agent_with_memory.main
"""

import asyncio
from radical.asyncflow import LocalExecutionBackend
from concurrent.futures import ThreadPoolExecutor
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.memory import MemoryManager, MemoryConfig
from flowgentic.utils.llm_providers import ChatLLMProvider
from .components.builder import WorkflowBuilder
from .utils.schemas import WorkflowState, MemoryStats
from langgraph.checkpoint.memory import InMemorySaver
from dotenv import load_dotenv

load_dotenv()


async def start_app():
	"""Initialize and run the memory-enabled sequential workflow."""

	print("=" * 80)
	print("🧠 MEMORY-ENABLED SEQUENTIAL WORKFLOW")
	print("=" * 80)
	print()

	# Initialize HPC backend
	backend = await LocalExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		# Initialize memory manager with importance-based strategy
		print("🔧 Initializing Memory Manager...")
		memory_config = MemoryConfig(
			max_short_term_messages=50,
			short_term_strategy="importance_based",  # Preserve most important messages
			context_window_buffer=10,
			memory_update_threshold=5,
			enable_summarization=False,  # Can be enabled for longer conversations
		)

		# Create LLM for potential summarization (if enabled)
		llm = ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash")

		memory_manager = MemoryManager(config=memory_config, llm=llm)

		print(f"   Strategy: {memory_config.short_term_strategy}")
		print(f"   Max messages: {memory_config.max_short_term_messages}")
		print(
			f"   Summarization: {'Enabled' if memory_config.enable_summarization else 'Disabled'}"
		)
		print()

		# Build workflow with memory integration
		print("🏗️ Building Workflow...")
		workflow_builder = WorkflowBuilder(agents_manager, memory_manager)
		workflow = workflow_builder.build_workflow()
		print()

		# Compile the app with checkpointer
		memory = InMemorySaver()
		app = workflow.compile(checkpointer=memory)

		# Initial state
		initial_state = WorkflowState(
			user_input="""
I need to research the latest developments in renewable energy storage technologies
and create a comprehensive report with recommendations for a clean energy startup
focusing on battery technologies, grid integration, and market opportunities.
            """.strip(),
			user_id="research_user_001",
		)

		print("🚀 Starting Memory-Enabled Sequential Workflow")
		print("=" * 80)
		print(f"📝 User Input: {initial_state.user_input[:100]}...")
		print()

		final_state = None
		try:
			# Execute workflow
			config = {"configurable": {"thread_id": "memory_workflow_1"}}
			async for chunk in app.astream(
				initial_state, config=config, stream_mode="values"
			):
				# Print stage updates
				if hasattr(chunk, "current_stage"):
					print(f"\n📍 Stage: {chunk.current_stage}")
				final_state = chunk

		except Exception as e:
			print(f"❌ Workflow execution failed: {str(e)}")
			raise

		# Record the final memory statistics in the state
		if final_state is not None:
			memory_stats = MemoryStats(**memory_manager.get_memory_health())
			# Handle both dict and Pydantic model
			if isinstance(final_state, dict):
				final_state["memory_stats"] = memory_stats.model_dump()
			else:
				final_state.memory_stats = memory_stats

		print("\n✅ Workflow completed successfully!")
		print("=" * 80)
		return final_state


if __name__ == "__main__":
	asyncio.run(start_app())
