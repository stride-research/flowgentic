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
	"""Contains all financial advisory workflow nodes."""

	def __init__(
		self, agents_manager: LangraphIntegration, tools_registry: ActionsRegistry
	) -> None:
		self.agents_manager = agents_manager
		self.tools_registry = tools_registry

	def get_all_nodes(self) -> Dict[str, callable]:
		"""Return all node functions for graph registration."""
		return {
			"validate_input": self.validate_input_node,
			"market_research_agent": self.market_research_agent_node,
			"risk_context_prep": self.risk_context_prep_node,
			"risk_assessment_agent": self.risk_assessment_agent_node,
			"strategy_context_prep": self.strategy_context_prep_node,
			"portfolio_strategy_agent": self.portfolio_strategy_agent_node,
			"finalize_report": self.finalize_report_node,
			"error_handler": self.error_handler_node,
		}

	@property
	def validate_input_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _validate_input_node(state: WorkflowState) -> WorkflowState:
			"""Validate and parse investment query."""
			print("🔄 Validation Node: Parsing investment query...")

			try:
				validation_task = self.tools_registry.get_function_task_by_name(
					"validate_investment_query"
				)(state.user_input)
				validation_data = await validation_task

				state.investment_validation = validation_data
				state.preprocessing_complete = validation_data.is_valid
				state.current_stage = "validation_complete"

				print(f"✅ Validation complete: {validation_data.word_count} words")
				print(f"   Risk Tolerance: {validation_data.risk_tolerance.value}")
				print(f"   Tickers Detected: {validation_data.detected_tickers}")

			except Exception as e:
				state.errors.append(f"Validation error: {str(e)}")
				state.current_stage = "validation_failed"

			return state

		return _validate_input_node

	@property
	def market_research_agent_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _market_research_agent_node(state: WorkflowState) -> WorkflowState:
			"""Market research agent execution node."""
			print("📊 Market Research Agent: Analyzing market conditions...")

			try:
				start_time = asyncio.get_event_loop().time()

				tools = [
					self.tools_registry.get_tool_by_name("get_market_data"),
					self.tools_registry.get_tool_by_name("analyze_sector_trends"),
					self.tools_registry.get_tool_by_name("economic_indicators"),
				]

				market_agent = create_react_agent(
					model=ChatLLMProvider(
						provider="OpenRouter", model="google/gemini-2.5-flash"
					),
					tools=tools,
				)

				research_prompt = f"""
				Analyze the investment opportunity based on the following query:
				{state.user_input}
				
				Risk Tolerance: {state.investment_validation.risk_tolerance.value}
				Time Horizon: {state.investment_validation.time_horizon}
				
				Use your tools to gather market data, analyze sector trends, and review economic indicators.
				Provide a comprehensive market analysis with key insights.
				Limit your research to 3-4 tool calls for efficiency.
				"""

				market_state = {
					"messages": [
						SystemMessage(
							content="You are a senior market research analyst specializing in investment analysis. "
							"Your job is to gather comprehensive market data, analyze trends, and provide data-driven insights. "
							"Always use tools to get current information. Explain your reasoning before each tool call."
						),
						HumanMessage(content=research_prompt),
					]
				}
				market_result = await market_agent.ainvoke(market_state)
				execution_time = asyncio.get_event_loop().time() - start_time

				if "messages" in market_result and isinstance(
					market_result["messages"], list
				):
					state.messages.extend(market_result["messages"])

				agent_output = AgentOutput(
					agent_name="Market Research Agent",
					output_content=market_result["messages"][-1].content,
					execution_time=execution_time,
					tools_used=[
						"get_market_data",
						"analyze_sector_trends",
						"economic_indicators",
					],
					success=True,
				)

				state.market_research_output = agent_output
				state.current_stage = "market_research_complete"

				print(f"✅ Market Research Agent complete in {execution_time:.2f}s")

				return state

			except Exception as e:
				error_msg = f"Market research agent error: {str(e)}"
				state.errors.append(error_msg)
				state.market_research_output = AgentOutput(
					agent_name="Market Research Agent",
					output_content="",
					execution_time=0,
					success=False,
					error_message=error_msg,
				)
				state.current_stage = "market_research_failed"

			return state

		return _market_research_agent_node

	@property
	def risk_context_prep_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _risk_context_prep_node(state: WorkflowState) -> WorkflowState:
			"""Prepare context for risk assessment."""
			print("🔧 Risk Context Prep: Preparing data for risk assessment...")

			try:
				context_task = self.tools_registry.get_function_task_by_name(
					"prepare_risk_context"
				)(state.market_research_output, state.investment_validation)
				context = await context_task

				state.portfolio_context = context
				state.current_stage = "risk_context_prepared"

				print("✅ Risk context preparation complete")

			except Exception as e:
				state.errors.append(f"Risk context preparation error: {str(e)}")
				state.current_stage = "risk_context_prep_failed"

			return state

		return _risk_context_prep_node

	@property
	def risk_assessment_agent_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _risk_assessment_agent_node(state: WorkflowState) -> WorkflowState:
			"""Risk assessment agent execution node."""
			print("⚠️  Risk Assessment Agent: Evaluating portfolio risks...")

			try:
				start_time = asyncio.get_event_loop().time()

				tools = [
					self.tools_registry.get_tool_by_name("calculate_portfolio_risk"),
					self.tools_registry.get_tool_by_name("regulatory_compliance_check"),
				]

				risk_agent = create_react_agent(
					model=ChatLLMProvider(
						provider="OpenRouter", model="google/gemini-2.5-flash"
					),
					tools=tools,
				)

				risk_prompt = f"""
				Based on the market research findings:
				{state.market_research_output.output_content[:500]}...
				
				Client Risk Profile:
				- Risk Tolerance: {state.investment_validation.risk_tolerance.value}
				- Time Horizon: {state.investment_validation.time_horizon}
				
				Use your tools to:
				1. Calculate comprehensive portfolio risk metrics
				2. Check regulatory compliance and suitability
				3. Provide risk assessment with clear warnings and recommendations
				
				Limit to 2-3 tool calls.
				"""

				risk_state = {
					"messages": [
						SystemMessage(
							content="You are a risk management specialist with expertise in portfolio risk analysis and compliance. "
							"Your job is to assess investment risks, calculate risk metrics, and ensure regulatory compliance. "
							"Explain your analytical approach before using tools."
						),
						HumanMessage(content=risk_prompt),
					]
				}
				risk_result = await risk_agent.ainvoke(risk_state)
				execution_time = asyncio.get_event_loop().time() - start_time

				state.messages.extend(risk_result["messages"])

				agent_output = AgentOutput(
					agent_name="Risk Assessment Agent",
					output_content=risk_result["messages"][-1].content,
					execution_time=execution_time,
					tools_used=[
						"calculate_portfolio_risk",
						"regulatory_compliance_check",
					],
					success=True,
				)

				state.risk_assessment_output = agent_output
				state.current_stage = "risk_assessment_complete"

				print(f"✅ Risk Assessment Agent complete in {execution_time:.2f}s")

			except Exception as e:
				error_msg = f"Risk assessment agent error: {str(e)}"
				state.errors.append(error_msg)
				state.risk_assessment_output = AgentOutput(
					agent_name="Risk Assessment Agent",
					output_content="",
					execution_time=0,
					success=False,
					error_message=error_msg,
				)
				state.current_stage = "risk_assessment_failed"

			return state

		return _risk_assessment_agent_node

	@property
	def strategy_context_prep_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _strategy_context_prep_node(state: WorkflowState) -> WorkflowState:
			"""Prepare context for portfolio strategy."""
			print("🔧 Strategy Context Prep: Preparing data for portfolio strategy...")

			try:
				context_task = self.tools_registry.get_function_task_by_name(
					"prepare_strategy_context"
				)(state.risk_assessment_output, state.portfolio_context)
				context = await context_task

				state.portfolio_context = context
				state.current_stage = "strategy_context_prepared"

				print("✅ Strategy context preparation complete")

			except Exception as e:
				state.errors.append(f"Strategy context preparation error: {str(e)}")
				state.current_stage = "strategy_context_prep_failed"

			return state

		return _strategy_context_prep_node

	@property
	def portfolio_strategy_agent_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _portfolio_strategy_agent_node(state: WorkflowState) -> WorkflowState:
			"""Portfolio strategy agent execution node."""
			print("📈 Portfolio Strategy Agent: Creating investment strategy...")

			try:
				start_time = asyncio.get_event_loop().time()

				tools = [
					self.tools_registry.get_tool_by_name("optimize_portfolio"),
					self.tools_registry.get_tool_by_name("generate_recommendations"),
					self.tools_registry.get_tool_by_name("generate_report_document"),
				]

				strategy_agent = create_react_agent(
					model=ChatLLMProvider(
						provider="OpenRouter", model="google/gemini-2.5-pro"
					),
					tools=tools,
				)

				strategy_prompt = f"""
				Create a comprehensive portfolio strategy based on:
				
				Market Research:
				{state.market_research_output.output_content[:400]}...
				
				Risk Assessment:
				{state.risk_assessment_output.output_content[:400]}...
				
				Client Profile:
				- Risk Tolerance: {state.investment_validation.risk_tolerance.value}
				- Time Horizon: {state.investment_validation.time_horizon}
				- Investment Amount: ${state.investment_validation.investment_amount or "Not specified"}
				
				Use your tools to:
				1. Optimize portfolio allocation
				2. Generate actionable recommendations
				3. Create a professional investment report document
				
				Provide a clear, actionable investment strategy.
				"""

				strategy_state = {
					"messages": [
						SystemMessage(
							content="You are a senior portfolio strategist specializing in creating optimized investment strategies. "
							"Your job is to synthesize market research and risk analysis into actionable portfolio recommendations. "
							"Always use tools to generate professional deliverables. Explain your strategy before each action."
						),
						HumanMessage(content=strategy_prompt),
					]
				}
				strategy_result = await strategy_agent.ainvoke(strategy_state)
				execution_time = asyncio.get_event_loop().time() - start_time

				state.messages.extend(strategy_result["messages"])

				agent_output = AgentOutput(
					agent_name="Portfolio Strategy Agent",
					output_content=strategy_result["messages"][-1].content,
					execution_time=execution_time,
					tools_used=[
						"optimize_portfolio",
						"generate_recommendations",
						"generate_report_document",
					],
					success=True,
				)

				state.portfolio_strategy_output = agent_output
				state.current_stage = "portfolio_strategy_complete"

				print(f"✅ Portfolio Strategy Agent complete in {execution_time:.2f}s")

			except Exception as e:
				error_msg = f"Portfolio strategy agent error: {str(e)}"
				state.errors.append(error_msg)
				state.portfolio_strategy_output = AgentOutput(
					agent_name="Portfolio Strategy Agent",
					output_content="",
					execution_time=0,
					success=False,
					error_message=error_msg,
				)
				state.current_stage = "portfolio_strategy_failed"

			return state

		return _portfolio_strategy_agent_node

	@property
	def finalize_report_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _finalize_report_node(state: WorkflowState) -> WorkflowState:
			"""Final report formatting node."""
			print("📄 Finalize Report: Formatting comprehensive investment report...")

			try:
				report_task = self.tools_registry.get_function_task_by_name(
					"format_investment_report"
				)(
					state.portfolio_strategy_output,
					state.risk_assessment_output,
					state.market_research_output,
					state.portfolio_context,
				)
				final_report = await report_task

				state.final_report = final_report
				state.workflow_complete = True
				state.current_stage = "completed"

				print("✅ Final investment report complete")
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
			state.final_report = f"Investment advisory workflow failed with errors: {'; '.join(state.errors)}"
			state.current_stage = "error_handled"
			return state

		return _error_handler_node
