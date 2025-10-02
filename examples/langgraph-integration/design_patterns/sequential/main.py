from radical.asyncflow import ConcurrentExecutionBackend
from concurrent.futures import ThreadPoolExecutor
from flowgentic.langGraph.main import LangraphIntegration
from .components.builder import WorkflowBuilder
from .utils.schemas import WorkflowState
import asyncio
from langgraph.checkpoint.memory import InMemorySaver
from flowgentic.langGraph.telemetry import GraphIntrospector


async def start_app():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		# Introspector
		introspector = GraphIntrospector()

		# Build workflow
		workflow_builder = WorkflowBuilder(agents_manager, introspector=introspector)
		workflow = workflow_builder.build_workflow()

		# Compile the app
		memory = InMemorySaver()
		app = workflow.compile(checkpointer=memory)

		# Initial state
		initial_state = WorkflowState(
			user_input="""
            I need to research the latest developments in renewable energy storage technologies 
            and create a comprehensive report with recommendations for a clean energy startup 
            focusing on battery technologies, grid integration, and market opportunities.
            """
		)

		print("🚀 Starting Sequential Agent Worklof")
		print("=" * 60)

		try:
			# Execute workflow
			config = {"configurable": {"thread_id": "1"}}
			async for chunk in app.astream(
				initial_state, config=config, stream_mode="values"
			):
				print(f"Chunk: {chunk}\n")

		except Exception as e:
			raise
			print(f"❌ Workflow execution failed: {str(e)}")
		finally:
			introspector.generate_report()
			await agents_manager.utils.render_graph(app)


if __name__ == "__main__":
	asyncio.run(start_app())
