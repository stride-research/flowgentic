from radical.asyncflow import ConcurrentExecutionBackend
from concurrent.futures import ThreadPoolExecutor
from flowgentic.langGraph.main import LangraphIntegration
from .components.builder import WorkflowBuilder
from .utils.schemas import WorkflowState
import asyncio
from langgraph.checkpoint.memory import InMemorySaver
from flowgentic.utils.telemetry import GraphIntrospector


async def start_app():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		# Build workflow
		workflow_builder = WorkflowBuilder(agents_manager)
		workflow = workflow_builder.build_workflow()

		# Compile the app
		memory = InMemorySaver()
		app = workflow.compile(checkpointer=memory)

		# Initial state with a sophisticated investment query
		initial_state = WorkflowState(
			user_input="""
			I'm looking to invest $250k with a moderate risk tolerance for long-term growth 
			(10+ years). I'm interested in a diversified portfolio that includes technology 
			stocks like AAPL and MSFT, but also want exposure to renewable energy and 
			healthcare sectors. I need a comprehensive investment strategy with risk 
			assessment and regulatory compliance review. Please analyze current market 
			conditions, evaluate portfolio risks, and provide specific allocation 
			recommendations with expected returns.
			"""
		)

		print("🚀 Starting Financial Advisory Sequential Workflow")
		print("=" * 80)

		try:
			# Execute workflow with increased recursion limit
			config = {
				"configurable": {"thread_id": "financial_advisory_1"},
				"recursion_limit": 50,  # Increase limit for complex agent interactions
			}
			async for chunk in app.astream(
				initial_state, config=config, stream_mode="values"
			):
				# Stream updates show progress through the workflow
				stage = chunk.get("current_stage", "unknown")
				print(f"\n📍 Current Stage: {stage}")

		except Exception as e:
			print(f"❌ Workflow execution failed: {str(e)}")
			raise
		finally:
			# Generate introspection report
			agents_manager.agent_introspector.generate_report()

			# Render the workflow graph
			await agents_manager.utils.render_graph(app)


if __name__ == "__main__":
	asyncio.run(start_app(), debug=True)
