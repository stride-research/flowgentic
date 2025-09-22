import asyncio
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional

from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.langGraph.agents import AsyncFlowType
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.utils.llm_providers import ChatLLMProvider

from dotenv import load_dotenv

load_dotenv()


class ValidationData(BaseModel):
	"""Model for input validation results."""

	is_valid: bool
	cleaned_input: str
	word_count: int
	timestamp: float
	metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
	"""Model for agent execution results."""

	agent_name: str
	output_content: str
	execution_time: float
	tools_used: List[str] = Field(default_factory=list)
	success: bool = True
	error_message: Optional[str] = None


class ContextData(BaseModel):
	"""Model for context passed between workflow stages."""

	previous_analysis: str
	input_metadata: ValidationData
	processing_stage: str
	agent_sequence: int
	additional_context: Dict[str, Any] = Field(default_factory=dict)


class WorkflowState(BaseModel):
	"""Main state model for the LangGraph workflow."""

	# Input
	user_input: str = ""

	# Preprocessing results
	validation_data: Optional[ValidationData] = None
	preprocessing_complete: bool = False

	# Agent execution results
	research_agent_output: Optional[AgentOutput] = None
	synthesis_agent_output: Optional[AgentOutput] = None

	# Context and intermediate data
	context: Optional[ContextData] = None

	# Final output
	final_output: str = ""
	workflow_complete: bool = False

	# Error handling
	errors: List[str] = Field(default_factory=list)
	current_stage: str = "initialized"


async def start_app():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		# REGISTERING AGENTS TOOLS:
		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.TOOL)
		async def web_search_tool(query: str) -> str:
			"""Search the web for information."""
			await asyncio.sleep(1)  # Simulate network delay
			return f"Search results for '{query}': Found relevant information about renewable energy storage, including battery technologies, grid integration, and market trends."

		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.TOOL)
		async def data_analysis_tool(data: str) -> Dict[str, Any]:
			"""Analyze data and return insights."""
			await asyncio.sleep(0.5)
			return {
				"insights": f"Analysis reveals key trends in '{data[:50]}...'",
				"confidence": 0.85,
				"key_points": [
					"Lithium-ion costs dropping 15% annually",
					"Grid-scale storage growing 40% YoY",
					"Solid-state batteries emerging as next breakthrough",
				],
				"market_size": "$12.1B by 2025",
			}

		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.TOOL)
		async def document_generator_tool(content: Dict[str, Any]) -> str:
			"""Generate a formatted document from analysis results."""
			await asyncio.sleep(0.3)
			key_points = content.get("key_points", [])
			return f"Executive Summary: Generated comprehensive report covering {len(key_points)} critical insights with {content.get('confidence', 0) * 100:.0f}% confidence level."

		# REGISTERING DETERMINISTC TASKS
		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.FUTURE)
		async def validate_input_task(user_input: str) -> ValidationData:
			"""Validate and preprocess user input - deterministic operation."""
			validation_result = ValidationData(
				is_valid=len(user_input.strip()) > 0,
				cleaned_input=user_input.strip(),
				word_count=len(user_input.split()),
				timestamp=asyncio.get_event_loop().time(),
				metadata={
					"has_keywords": any(
						word in user_input.lower()
						for word in ["research", "analyze", "report"]
					),
					"complexity_score": min(len(user_input.split()) / 10, 1.0),
					"domain": "energy" if "energy" in user_input.lower() else "general",
				},
			)
			return validation_result

		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.FUTURE)
		async def prepare_context_task(
			research_output: AgentOutput, validation_data: ValidationData
		) -> ContextData:
			"""Prepare context for the second agent - deterministic operation."""
			context = ContextData(
				previous_analysis=research_output.output_content,
				input_metadata=validation_data,
				processing_stage="context_preparation",
				agent_sequence=1,
				additional_context={
					"research_tools_used": research_output.tools_used,
					"research_execution_time": research_output.execution_time,
					"input_complexity": validation_data.metadata.get(
						"complexity_score", 0
					),
				},
			)
			return context

		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.FUTURE)
		async def format_final_output_task(
			synthesis_output: AgentOutput, context: ContextData
		) -> str:
			"""Format the final output - deterministic operation."""
			formatted_output = f"""
                  === SEQUENTIAL REACT AGENT WORKFLOW RESULTS ===
                  Input processed at: {context.input_metadata.timestamp}
                  Word count: {context.input_metadata.word_count}
                  Domain: {context.input_metadata.metadata.get("domain", "unknown")}

                  === RESEARCH AGENT OUTPUT ===
                  Agent: {context.additional_context.get("research_agent_name", "Research Agent")}
                  Tools Used: {", ".join(context.additional_context.get("research_tools_used", []))}
                  Execution Time: {context.additional_context.get("research_execution_time", 0):.2f}s

                  {context.previous_analysis}

                  === SYNTHESIS AGENT OUTPUT ===
                  Agent: {synthesis_output.agent_name}
                  Tools Used: {", ".join(synthesis_output.tools_used)}
                  Execution Time: {synthesis_output.execution_time:.2f}s

                  {synthesis_output.output_content}

                  === WORKFLOW COMPLETE ===
                  Total Processing Time: {synthesis_output.execution_time + context.additional_context.get("research_execution_time", 0):.2f}s
                  """
			return formatted_output.strip()

		# CREATING (SUBGRAPH) NODES IN THE GRAPH
		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.BLOCK)
		async def preprocess_node(state: WorkflowState) -> WorkflowState:
			"""Preprocessing node with parallel validation and metadata extraction."""
			print("🔄 Preprocessing Node: Starting input validation...")

			try:
				# Fan-out: Start validation task
				validation_task = validate_input_task(state.user_input)

				# Could add more parallel preprocessing here
				# security_task = security_scan_task(state.user_input)
				# metadata_task = extract_metadata_task(state.user_input)

				# Fan-in: Wait for validation to complete
				validation_data = await validation_task

				# Update state
				state.validation_data = validation_data
				state.preprocessing_complete = True
				state.current_stage = "preprocessing_complete"

				print(
					f"✅ Preprocessing complete: {validation_data.word_count} words, domain: {validation_data.metadata.get('domain')}"
				)

			except Exception as e:
				state.errors.append(f"Preprocessing error: {str(e)}")
				state.current_stage = "preprocessing_failed"

			return state

		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.BLOCK)
		async def research_agent_node(state: WorkflowState) -> WorkflowState:
			"""Research agent execution node."""
			print("🔍 Research Agent Node: Starting research and analysis...")

			try:
				start_time = asyncio.get_event_loop().time()

				# Create research agent with tools
				research_agent = create_react_agent(
					model=ChatLLMProvider(
						provider="OpenRouter", model="google/gemini-2.5-flash"
					),
					tools=[web_search_tool, data_analysis_tool],
				)

				# Execute research agent
				research_state = {
					"messages": [
						SystemMessage(
							content="You are a research agent specializing in technology analysis. Your job is to gather comprehensive information, analyze data, and provide detailed insights. Always use your tools to get the most current and accurate information."
						),
						HumanMessage(content=state.user_input),
					]
				}
				research_result = await research_agent.ainvoke(research_state)

				execution_time = asyncio.get_event_loop().time() - start_time

				# Extract tools used (simplified - in real implementation, you'd track this properly)
				tools_used = ["web_search_tool", "data_analysis_tool"]

				# Create agent output model
				agent_output = AgentOutput(
					agent_name="Research Agent",
					output_content=research_result["messages"][-1].content,
					execution_time=execution_time,
					tools_used=tools_used,
					success=True,
				)

				# Update state
				state.research_agent_output = agent_output
				state.current_stage = "research_complete"

				print(f"✅ Research Agent complete in {execution_time:.2f}s")

			except Exception as e:
				error_msg = f"Research agent error: {str(e)}"
				state.errors.append(error_msg)
				state.research_agent_output = AgentOutput(
					agent_name="Research Agent",
					output_content="",
					execution_time=0,
					success=False,
					error_message=error_msg,
				)
				state.current_stage = "research_failed"

			return state

		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.BLOCK)
		async def context_preparation_node(state: WorkflowState) -> WorkflowState:
			"""Context preparation node - runs in parallel with other deterministic tasks."""
			print(
				"🔧 Context Preparation Node: Preparing context for synthesis agent..."
			)

			try:
				# Prepare context using deterministic task
				context_task = prepare_context_task(
					state.research_agent_output, state.validation_data
				)
				context = await context_task

				# Update state
				state.context = context
				state.current_stage = "context_prepared"

				print("✅ Context preparation complete")

			except Exception as e:
				state.errors.append(f"Context preparation error: {str(e)}")
				state.current_stage = "context_preparation_failed"

			return state

		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.BLOCK)
		async def synthesis_agent_node(state: WorkflowState) -> WorkflowState:
			"""Synthesis agent execution node."""
			print("🏗️ Synthesis Agent Node: Creating final deliverables...")

			try:
				start_time = asyncio.get_event_loop().time()

				# Create synthesis agent
				synthesis_agent = create_react_agent(
					model=ChatLLMProvider(
						provider="OpenRouter", model="google/gemini-2.5-flash"
					),
					tools=[document_generator_tool],
				)

				# Prepare enriched input for synthesis agent
				synthesis_input = f"""
                        Based on the research findings: {state.research_agent_output.output_content}
                        
                        Context: {state.context.dict() if state.context else "No context available"}
                        
                        Please create a comprehensive synthesis with clear recommendations for a clean energy startup focusing on renewable energy storage technologies.
                        """

				# Execute synthesis agent
				synthesis_state = {
					"messages": [
						SystemMessage(
							content="You are a synthesis agent specializing in creating comprehensive reports and deliverables. Your job is to take research findings and create polished, actionable documents with clear recommendations."
						),
						HumanMessage(content=synthesis_input),
					]
				}
				synthesis_result = await synthesis_agent.ainvoke(synthesis_state)

				execution_time = asyncio.get_event_loop().time() - start_time

				# Create agent output model
				agent_output = AgentOutput(
					agent_name="Synthesis Agent",
					output_content=synthesis_result["messages"][-1].content,
					execution_time=execution_time,
					tools_used=["document_generator_tool"],
					success=True,
				)

				# Update state
				state.synthesis_agent_output = agent_output
				state.current_stage = "synthesis_complete"

				print(f"✅ Synthesis Agent complete in {execution_time:.2f}s")

			except Exception as e:
				error_msg = f"Synthesis agent error: {str(e)}"
				state.errors.append(error_msg)
				state.synthesis_agent_output = AgentOutput(
					agent_name="Synthesis Agent",
					output_content="",
					execution_time=0,
					success=False,
					error_message=error_msg,
				)
				state.current_stage = "synthesis_failed"

			return state

		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.BLOCK)
		async def finalize_output_node(state: WorkflowState) -> WorkflowState:
			"""Final output formatting node."""
			print("📄 Finalize Output Node: Formatting final results...")

			try:
				# Format final output using deterministic task
				final_output_task = format_final_output_task(
					state.synthesis_agent_output, state.context
				)
				final_output = await final_output_task

				# Update state
				state.final_output = final_output
				state.workflow_complete = True
				state.current_stage = "completed"

				print("✅ Final output formatting complete")

			except Exception as e:
				state.errors.append(f"Final output formatting error: {str(e)}")
				state.current_stage = "finalization_failed"

			return state

		@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.BLOCK)
		async def error_handler_node(state: WorkflowState) -> WorkflowState:
			"""Handle errors in the workflow."""
			print(f"❌ Error Handler: {'; '.join(state.errors)}")
			state.final_output = (
				f"Workflow failed with errors: {'; '.join(state.errors)}"
			)
			state.current_stage = "error_handled"
			return state

		def should_continue_after_preprocessing(state: WorkflowState) -> str:
			"""Determine next step after preprocessing."""
			if state.preprocessing_complete and state.validation_data.is_valid:
				return "research_agent"
			else:
				return "error_handler"

		def should_continue_after_research(state: WorkflowState) -> str:
			"""Determine next step after research agent."""
			if state.research_agent_output and state.research_agent_output.success:
				return "context_preparation"
			else:
				return "error_handler"

		def should_continue_after_context(state: WorkflowState) -> str:
			"""Determine next step after context preparation."""
			if state.context:
				return "synthesis_agent"
			else:
				return "error_handler"

		def should_continue_after_synthesis(state: WorkflowState) -> str:
			"""Determine next step after synthesis agent."""
			if state.synthesis_agent_output and state.synthesis_agent_output.success:
				return "finalize_output"
			else:
				return "error_handler"

		# Create state graph
		workflow = StateGraph(WorkflowState)

		# Add nodes
		workflow.add_node("preprocess", preprocess_node)
		workflow.add_node("research_agent", research_agent_node)
		workflow.add_node("context_preparation", context_preparation_node)
		workflow.add_node("synthesis_agent", synthesis_agent_node)
		workflow.add_node("finalize_output", finalize_output_node)
		workflow.add_node("error_handler", error_handler_node)

		# Add conditional edges
		workflow.add_conditional_edges(
			"preprocess",
			should_continue_after_preprocessing,
			{"research_agent": "research_agent", "error_handler": "error_handler"},
		)

		workflow.add_conditional_edges(
			"research_agent",
			should_continue_after_research,
			{
				"context_preparation": "context_preparation",
				"error_handler": "error_handler",
			},
		)

		workflow.add_conditional_edges(
			"context_preparation",
			should_continue_after_context,
			{"synthesis_agent": "synthesis_agent", "error_handler": "error_handler"},
		)

		workflow.add_conditional_edges(
			"synthesis_agent",
			should_continue_after_synthesis,
			{"finalize_output": "finalize_output", "error_handler": "error_handler"},
		)

		# Add edges to END
		workflow.add_edge("finalize_output", END)
		workflow.add_edge("error_handler", END)

		# Set entry point
		workflow.set_entry_point("preprocess")

		app = workflow.compile()

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


asyncio.run(start_app())
