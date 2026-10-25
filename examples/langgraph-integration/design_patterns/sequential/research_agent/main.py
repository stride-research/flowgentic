from radical.asyncflow import LocalExecutionBackend
from concurrent.futures import ThreadPoolExecutor
from flowgentic.langGraph.main import LangraphIntegration
from .components.builder import WorkflowBuilder
from .utils.schemas import WorkflowState
import asyncio
from langgraph.checkpoint.memory import InMemorySaver


# --8<-- [start:start-app]
async def start_app():
	backend = await LocalExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		# Build workflow
		workflow_builder = WorkflowBuilder(agents_manager)
		workflow = workflow_builder.build_workflow()

		# Compile the app
		memory = InMemorySaver()
		app = workflow.compile(checkpointer=memory)
# --8<-- [end:start-app]

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
			final_state = None
			async for chunk in app.astream(
				initial_state, config=config, stream_mode="values"
			):
				print(f"Chunk: {chunk}\n")
				final_state = chunk

		except Exception as e:
			raise
			print(f"❌ Workflow execution failed: {str(e)}")

		# The nodes route their failures to the error handler, so a failed run
		# raises here, or it would exit 0
		if not final_state["workflow_complete"]:
			raise RuntimeError(f"Workflow failed: {'; '.join(final_state['errors'])}")
		return final_state


if __name__ == "__main__":
	asyncio.run(start_app(), debug=True)
