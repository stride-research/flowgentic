"""
Sales Analytics Sequential Workflow with MCP Integration

Demonstrates FlowGentic's MCP integration in a production-like sequential pipeline:
- Stage 1: Query Validation
- Stage 2: Data Extraction (MCP SQLite Toolbox specialist)
- Stage 3: Context Prep → Analytics Agent (statistical analysis)
- Stage 4: Context Prep → Report Generation Agent
- Stage 5: Finalization

Shows:
- MCP database specialist using langchain-mcp-tools SQLite server
- 3 LLM agents coordinating through shared state
- Context preparation tasks between stages
- Complex state management
- Full telemetry and introspection
"""

from radical.asyncflow import ConcurrentExecutionBackend
from concurrent.futures import ThreadPoolExecutor
from flowgentic.langGraph.main import LangraphIntegration
from .components.builder import WorkflowBuilder
from .utils.schemas import WorkflowState
import asyncio
from langgraph.checkpoint.memory import InMemorySaver


async def start_app():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		# Build workflow
		workflow_builder = WorkflowBuilder(agents_manager)
		workflow = workflow_builder.build_workflow()

		# Compile the app
		memory = InMemorySaver()
		app = workflow.compile(checkpointer=memory)

		# Initial state with sales analytics query
		initial_state = WorkflowState(
			user_query="""
            Analyze our sales performance for the North region over the last year.
            I need a comprehensive report including revenue trends, top performing 
            products, sales representative performance, and any anomalies or patterns 
            in the data. Include growth rate analysis and recommendations for 
            improving performance in underperforming categories.
            """
		)

		print("🚀 Starting Sales Analytics Sequential Workflow (MCP Integration)")
		print("=" * 80)

		try:
			# Execute workflow
			config = {
				"configurable": {"thread_id": "sales_analytics_1"},
				"recursion_limit": 50,
			}
			final_state = None
			async for chunk in app.astream(
				initial_state, config=config, stream_mode="values"
			):
				# Stream updates show progress through the workflow
				stage = chunk.get("current_stage", "unknown")
				print(f"\n📍 Current Stage: {stage}")
				final_state = chunk

		except Exception as e:
			print(f"❌ Workflow execution failed: {str(e)}")
			raise
		finally:
			# Generate all execution artifacts (directories, report, graph)
			await agents_manager.generate_execution_artifacts(
				app, __file__, final_state=final_state
			)


if __name__ == "__main__":
	asyncio.run(start_app(), debug=True)
