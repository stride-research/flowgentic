from radical.asyncflow import ConcurrentExecutionBackend
from concurrent.futures import ThreadPoolExecutor
from flowgentic.langGraph.main import LangraphIntegration
from .components.builder import WorkflowBuilder
from .utils.schemas import WorkflowState
import asyncio


async def start_app():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		# Build workflow
		workflow_builder = WorkflowBuilder(agents_manager)
		workflow = workflow_builder.build_workflow()

		# Compile the app
		app = workflow.compile()

		# Render graph (optional)
		await agents_manager.utils.render_graph(app)

		# Initial state
		initial_state = WorkflowState(
			user_input="""
            I need to research the latest developments in renewable energy storage technologies 
            and create a comprehensive report with recommendations for a clean energy startup 
            focusing on battery technologies, grid integration, and market opportunities.
            """
		)

		print("🚀 Starting Sequential ReAct Agent Workflow")
		print("=" * 60)

		try:
			# Execute workflow
			async for chunk in app.astream(initial_state, stream_mode="values"):
				print(f"Chunk: {chunk}\n")

		except Exception as e:
			print(f"❌ Workflow execution failed: {str(e)}")


if __name__ == "__main__":
	asyncio.run(start_app())
