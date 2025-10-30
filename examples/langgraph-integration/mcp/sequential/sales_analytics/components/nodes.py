"""
All workflow nodes for sales analytics pipeline.
3 LLM agents + 2 context prep nodes + validation + finalization + error handling.
"""

import sys
from typing import Dict

from flowgentic.langGraph.main import LangraphIntegration
from ..utils.schemas import WorkflowState, AgentOutput
from .utils.actions_registry import ActionsRegistry

import asyncio
from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.utils.llm_providers import ChatLLMProvider
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage

import logging

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class WorkflowNodes:
	"""Contains all sales analytics workflow nodes."""

	def __init__(
		self, agents_manager: LangraphIntegration, tools_registry: ActionsRegistry
	) -> None:
		self.agents_manager = agents_manager
		self.tools_registry = tools_registry

	def get_all_nodes(self) -> Dict[str, callable]:
		"""Return all node functions for graph registration."""
		return {
			"validate_query": self.validate_query_node,
			"data_extraction_agent": self.data_extraction_agent_node,
			"analytics_context_prep": self.analytics_context_prep_node,
			"analytics_agent": self.analytics_agent_node,
			"report_context_prep": self.report_context_prep_node,
			"report_generation_agent": self.report_generation_agent_node,
			"finalize_report": self.finalize_report_node,
			"error_handler": self.error_handler_node,
		}

	@property
	def validate_query_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _validate_query_node(state: WorkflowState) -> WorkflowState:
			"""Validate and parse sales analytics query."""
			print("🔄 Validation Node: Parsing sales query...")

			try:
				validation_task = self.tools_registry.get_function_task_by_name(
					"validate_query"
				)(state.user_query)
				validation_data = await validation_task

				state.query_validation = validation_data
				state.validation_complete = validation_data.is_valid
				state.current_stage = "validation_complete"

				print(f"✅ Validation complete: {validation_data.word_count} words")
				print(
					f"   Analysis Type: {validation_data.analysis_type.value if validation_data.analysis_type else 'General'}"
				)
				print(
					f"   Regions: {', '.join(validation_data.regions) if validation_data.regions else 'All'}"
				)

			except Exception as e:
				state.errors.append(f"Validation error: {str(e)}")
				state.current_stage = "validation_failed"

			return state

		return _validate_query_node

	@property
	def data_extraction_agent_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _data_extraction_agent_node(state: WorkflowState) -> WorkflowState:
			"""Data extraction agent using MCP SQLite specialist."""
			print("📊 Data Extraction Agent (MCP): Querying sales database...")

			try:
				start_time = asyncio.get_event_loop().time()

				# Get MCP database specialist
				database_specialist = self.tools_registry.get_tool_by_name(
					"database_specialist"
				)

				# Create ReAct agent with MCP tool
				data_agent = create_react_agent(
					model=ChatLLMProvider(
						provider="OpenRouter", model="google/gemini-2.5-flash"
					),
					tools=[database_specialist],
				)

				data_prompt = f"""
Analyze sales data based on the following query:
{state.user_query}

Query Parameters:
- Analysis Type: {state.query_validation.analysis_type.value if state.query_validation.analysis_type else "General"}
- Time Period: {state.query_validation.time_period}
- Regions: {", ".join(state.query_validation.regions) if state.query_validation.regions else "All"}

Use the database_specialist tool to query the sales database.
Tables available: sales, products, sales_reps

Extract:
1. Total revenue and sales metrics
2. Regional performance data
3. Product category breakdowns
4. Sales representative performance

Limit your queries to 2-3 tool calls for efficiency.
"""

				data_state = {
					"messages": [
						SystemMessage(
							content="You are a data extraction specialist using an MCP database tool. "
							"Your job is to query the sales database and extract relevant data. "
							"Always explain your query strategy before using tools."
						),
						HumanMessage(content=data_prompt),
					]
				}

				data_result = await data_agent.ainvoke(data_state)
				execution_time = asyncio.get_event_loop().time() - start_time

				if "messages" in data_result and isinstance(
					data_result["messages"], list
				):
					state.messages.extend(data_result["messages"])

				agent_output = AgentOutput(
					agent_name="Data Extraction Agent (MCP)",
					output_content=data_result["messages"][-1].content,
					execution_time=execution_time,
					tools_used=["database_specialist (MCP SQLite Toolbox)"],
					success=True,
				)

				state.data_extraction_output = agent_output
				state.current_stage = "data_extraction_complete"

				print(f"✅ Data Extraction Agent complete in {execution_time:.2f}s")

				return state

			except Exception as e:
				error_msg = f"Data extraction agent error: {str(e)}"
				state.errors.append(error_msg)
				state.data_extraction_output = AgentOutput(
					agent_name="Data Extraction Agent (MCP)",
					output_content="",
					execution_time=0,
					success=False,
					error_message=error_msg,
				)
				state.current_stage = "data_extraction_failed"

			return state

		return _data_extraction_agent_node

	@property
	def analytics_context_prep_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _analytics_context_prep_node(state: WorkflowState) -> WorkflowState:
			"""Prepare context for analytics agent."""
			print("🔧 Analytics Context Prep: Preparing data for analysis...")

			try:
				context_task = self.tools_registry.get_function_task_by_name(
					"prepare_analytics_context"
				)(state.data_extraction_output, state.query_validation)
				context = await context_task

				state.analysis_context = context
				state.current_stage = "analytics_context_prepared"

				print("✅ Analytics context preparation complete")

			except Exception as e:
				state.errors.append(f"Analytics context preparation error: {str(e)}")
				state.current_stage = "analytics_context_prep_failed"

			return state

		return _analytics_context_prep_node

	@property
	def analytics_agent_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _analytics_agent_node(state: WorkflowState) -> WorkflowState:
			"""Analytics agent performs statistical analysis."""
			print("📈 Analytics Agent: Performing statistical analysis...")

			try:
				start_time = asyncio.get_event_loop().time()

				tools = [
					self.tools_registry.get_tool_by_name("calculate_growth_rate"),
					self.tools_registry.get_tool_by_name("detect_anomalies"),
					self.tools_registry.get_tool_by_name(
						"calculate_performance_metrics"
					),
					self.tools_registry.get_tool_by_name("trend_analysis"),
				]

				analytics_agent = create_react_agent(
					model=ChatLLMProvider(
						provider="OpenRouter", model="google/gemini-2.5-flash"
					),
					tools=tools,
				)

				analytics_prompt = f"""
Based on the extracted sales data:
{state.data_extraction_output.output_content[:500]}...

Perform comprehensive statistical analysis:
1. Calculate growth rates and trends
2. Detect anomalies in sales patterns
3. Calculate key performance metrics
4. Analyze time series trends

Use your analytical tools to provide data-driven insights.
Limit to 3-4 tool calls.
"""

				analytics_state = {
					"messages": [
						SystemMessage(
							content="You are a statistical analyst specializing in sales analytics. "
							"Your job is to analyze data and provide actionable insights. "
							"Explain your analytical approach before using tools."
						),
						HumanMessage(content=analytics_prompt),
					]
				}

				analytics_result = await analytics_agent.ainvoke(analytics_state)
				execution_time = asyncio.get_event_loop().time() - start_time

				state.messages.extend(analytics_result["messages"])

				agent_output = AgentOutput(
					agent_name="Analytics Agent",
					output_content=analytics_result["messages"][-1].content,
					execution_time=execution_time,
					tools_used=[
						"calculate_growth_rate",
						"detect_anomalies",
						"calculate_performance_metrics",
						"trend_analysis",
					],
					success=True,
				)

				state.analytics_output = agent_output
				state.current_stage = "analytics_complete"

				print(f"✅ Analytics Agent complete in {execution_time:.2f}s")

			except Exception as e:
				error_msg = f"Analytics agent error: {str(e)}"
				state.errors.append(error_msg)
				state.analytics_output = AgentOutput(
					agent_name="Analytics Agent",
					output_content="",
					execution_time=0,
					success=False,
					error_message=error_msg,
				)
				state.current_stage = "analytics_failed"

			return state

		return _analytics_agent_node

	@property
	def report_context_prep_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _report_context_prep_node(state: WorkflowState) -> WorkflowState:
			"""Prepare context for report generation."""
			print("🔧 Report Context Prep: Preparing data for report generation...")

			try:
				context_task = self.tools_registry.get_function_task_by_name(
					"prepare_report_context"
				)(state.analytics_output, state.analysis_context)
				context = await context_task

				state.analysis_context = context
				state.current_stage = "report_context_prepared"

				print("✅ Report context preparation complete")

			except Exception as e:
				state.errors.append(f"Report context preparation error: {str(e)}")
				state.current_stage = "report_context_prep_failed"

			return state

		return _report_context_prep_node

	@property
	def report_generation_agent_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _report_generation_agent_node(state: WorkflowState) -> WorkflowState:
			"""Report generation agent creates final deliverables."""
			print("📄 Report Generation Agent: Creating final report...")

			try:
				start_time = asyncio.get_event_loop().time()

				tools = [
					self.tools_registry.get_tool_by_name("generate_executive_summary"),
					self.tools_registry.get_tool_by_name("create_visualizations"),
					self.tools_registry.get_tool_by_name("format_recommendations"),
				]

				report_agent = create_react_agent(
					model=ChatLLMProvider(
						provider="OpenRouter", model="google/gemini-2.5-flash"
					),
					tools=tools,
				)

				report_prompt = f"""
Create a comprehensive sales analytics report based on:

Data Extraction Results:
{state.data_extraction_output.output_content[:400]}...

Analytics Insights:
{state.analytics_output.output_content[:400]}...

IMPORTANT: Call tools ONE AT A TIME. Do not concatenate tool names.
Use your tools to:
1. First, call generate_executive_summary
2. Then, call create_visualizations  
3. Finally, call format_recommendations

Provide a clear, professional report suitable for business stakeholders.
"""

				report_state = {
					"messages": [
						SystemMessage(
							content="You are a business intelligence report writer. "
							"Your job is to synthesize data and analytics into actionable reports. "
							"Always use tools to generate professional deliverables."
						),
						HumanMessage(content=report_prompt),
					]
				}

				report_result = await report_agent.ainvoke(report_state)
				execution_time = asyncio.get_event_loop().time() - start_time

				state.messages.extend(report_result["messages"])

				agent_output = AgentOutput(
					agent_name="Report Generation Agent",
					output_content=report_result["messages"][-1].content,
					execution_time=execution_time,
					tools_used=[
						"generate_executive_summary",
						"create_visualizations",
						"format_recommendations",
					],
					success=True,
				)

				state.report_generation_output = agent_output
				state.current_stage = "report_generation_complete"

				print(f"✅ Report Generation Agent complete in {execution_time:.2f}s")

			except Exception as e:
				error_msg = f"Report generation agent error: {str(e)}"
				state.errors.append(error_msg)
				state.report_generation_output = AgentOutput(
					agent_name="Report Generation Agent",
					output_content="",
					execution_time=0,
					success=False,
					error_message=error_msg,
				)
				state.current_stage = "report_generation_failed"

			return state

		return _report_generation_agent_node

	@property
	def finalize_report_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _finalize_report_node(state: WorkflowState) -> WorkflowState:
			"""Final report formatting node."""
			print("📄 Finalize Report: Formatting comprehensive analytics report...")

			try:
				report_task = self.tools_registry.get_function_task_by_name(
					"format_final_report"
				)(
					state.report_generation_output,
					state.analytics_output,
					state.data_extraction_output,
					state.analysis_context,
				)
				final_report = await report_task

				state.final_report = final_report
				state.workflow_complete = True
				state.current_stage = "completed"

				print("✅ Final sales analytics report complete")
				print("\n" + "=" * 80)
				print(final_report[:500] + "...")
				print("=" * 80 + "\n")

			except Exception as e:
				state.errors.append(f"Report finalization error: {str(e)}")
				state.current_stage = "finalization_failed"

			return state

		return _finalize_report_node

	@property
	def error_handler_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _error_handler_node(state: WorkflowState) -> WorkflowState:
			"""Handle errors in the workflow."""
			print(f"❌ Error Handler: {'; '.join(state.errors)}")
			state.final_report = f"Sales analytics workflow failed with errors: {'; '.join(state.errors)}"
			state.current_stage = "error_handled"
			return state

		return _error_handler_node
