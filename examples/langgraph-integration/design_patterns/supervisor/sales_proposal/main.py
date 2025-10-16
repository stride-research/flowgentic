"""
Sales Proposal Generation - Supervisor Pattern Example

This example demonstrates a sophisticated supervisor pattern where a supervisor agent
orchestrates multiple specialized agents to generate a comprehensive sales proposal.

The workflow includes:
- Supervisor Agent: Coordinates the overall proposal generation process
- Data Analysis Agent: Pulls performance metrics and customer history
- Market Research Agent: Identifies competitive advantages and industry trends
- Document Creation Agent: Assembles the final proposal document

All execution happens through radical asyncflow for HPC compatibility.
"""

import pathlib
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from radical.asyncflow import ConcurrentExecutionBackend
from concurrent.futures import ThreadPoolExecutor
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.utils.llm_providers import ChatLLMProvider

from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import BaseMessage, HumanMessage
from typing import Annotated, List, Optional, Dict
import asyncio
import json
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()


# ==================== State Definition ====================


class ProposalData(BaseModel):
	"""Data structure for the sales proposal."""

	customer_name: str = ""
	customer_history: Dict = Field(default_factory=dict)
	performance_metrics: Dict = Field(default_factory=dict)
	market_insights: Dict = Field(default_factory=dict)
	competitive_advantages: List[str] = Field(default_factory=list)
	final_proposal: str = ""


class WorkflowState(BaseModel):
	"""State for the sales proposal workflow."""

	messages: Annotated[List[BaseMessage], add_messages] = []
	proposal_data: ProposalData = Field(default_factory=ProposalData)
	customer_name: str = ""
	product_category: str = ""


# ==================== Workflow Creation ====================


def create_sales_proposal_workflow(agents_manager: LangraphIntegration):
	"""Build a sales proposal workflow with supervisor orchestration."""

	# ==================== Data Analysis Agent Tools ====================

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
	)
	async def fetch_customer_history(customer_name: str) -> str:
		"""Fetch historical data for a customer including past purchases and interactions."""
		await asyncio.sleep(0.5)  # Simulate database query

		history = {
			"customer_since": "2021-03-15",
			"total_purchases": 12,
			"total_revenue": "$245,000",
			"last_purchase": "2024-09-22",
			"primary_contact": "Jane Smith, VP Operations",
			"account_status": "Premium",
			"past_products": [
				"Enterprise Suite v2.1",
				"Analytics Dashboard Pro",
				"API Integration Package",
			],
			"support_tickets": 8,
			"satisfaction_score": 4.7,
		}

		return json.dumps(history, indent=2)

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
	)
	async def analyze_performance_metrics(customer_name: str) -> str:
		"""Analyze performance metrics and ROI for the customer."""
		await asyncio.sleep(0.6)  # Simulate analysis

		metrics = {
			"roi_analysis": {
				"current_roi": "285%",
				"time_to_value": "3.2 months",
				"cost_savings": "$127,000/year",
			},
			"usage_metrics": {
				"daily_active_users": 45,
				"monthly_active_users": 78,
				"feature_adoption_rate": "82%",
				"api_calls_per_month": "1.2M",
			},
			"growth_metrics": {
				"user_growth": "+34% YoY",
				"revenue_growth": "+28% YoY",
				"engagement_increase": "+41% QoQ",
			},
			"benchmark_comparison": "Performing 23% above industry average",
		}

		return json.dumps(metrics, indent=2)

	# ==================== Market Research Agent Tools ====================

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
	)
	async def research_industry_trends(industry: str) -> str:
		"""Research current trends and forecasts for a specific industry."""
		await asyncio.sleep(0.7)  # Simulate research

		trends = {
			"industry": industry,
			"key_trends": [
				{
					"trend": "AI-Driven Automation",
					"impact": "High",
					"adoption_rate": "67%",
					"forecast": "Expected to reach 89% by 2026",
				},
				{
					"trend": "Real-time Data Analytics",
					"impact": "Very High",
					"adoption_rate": "54%",
					"forecast": "Critical capability for competitive advantage",
				},
				{
					"trend": "Cloud-Native Architecture",
					"impact": "High",
					"adoption_rate": "78%",
					"forecast": "Industry standard by 2025",
				},
			],
			"market_size": "$12.4B (2024) → $18.7B (2027)",
			"growth_rate": "15.2% CAGR",
			"key_drivers": [
				"Digital transformation initiatives",
				"Remote work sustainability",
				"Data privacy regulations",
			],
		}

		return json.dumps(trends, indent=2)

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
	)
	async def analyze_competitors(product_category: str) -> str:
		"""Analyze competitive landscape and identify our advantages."""
		await asyncio.sleep(0.6)  # Simulate competitive analysis

		analysis = {
			"category": product_category,
			"main_competitors": [
				{
					"name": "CompetitorX",
					"market_share": "28%",
					"weakness": "Poor integration capabilities",
				},
				{
					"name": "MarketLeader Pro",
					"market_share": "35%",
					"weakness": "High pricing, limited customization",
				},
				{
					"name": "StartupY",
					"market_share": "12%",
					"weakness": "Unproven scalability",
				},
			],
			"our_competitive_advantages": [
				"Superior API integration (3x faster implementation)",
				"30% lower TCO than market leader",
				"Enterprise-grade security with SOC 2 Type II",
				"99.99% uptime SLA vs industry avg 99.5%",
				"24/7 dedicated support included",
				"AI-powered predictive analytics (unique feature)",
				"Modular architecture for custom workflows",
			],
			"market_positioning": "Premium value with enterprise reliability",
			"pricing_advantage": "25-30% more cost-effective at scale",
		}

		return json.dumps(analysis, indent=2)

	# ==================== Document Creation Agent Tools ====================

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
	)
	async def create_executive_summary(
		customer_name: str, key_metrics: str, competitive_advantages: str
	) -> str:
		"""Create an executive summary section for the proposal."""
		await asyncio.sleep(0.4)

		summary = f"""
## EXECUTIVE SUMMARY

Dear {customer_name} Leadership Team,

We are pleased to present this comprehensive proposal for expanding our partnership. 
Based on your current success metrics and the evolving market landscape, we've identified 
significant opportunities for enhanced value delivery.

### Current Performance Highlights
{key_metrics}

### Strategic Value Proposition
{competitive_advantages}

This proposal outlines a solution designed specifically for your organization's growth 
trajectory and strategic objectives.
"""
		return summary.strip()

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
	)
	async def create_technical_section(
		customer_history: str, industry_trends: str
	) -> str:
		"""Create the technical capabilities and solutions section."""
		await asyncio.sleep(0.4)

		section = f"""
## TECHNICAL SOLUTION OVERVIEW

### Your Current Environment
{customer_history}

### Industry Context & Future-Proofing
{industry_trends}

### Proposed Technical Enhancements
- **Advanced Analytics Module**: Real-time dashboards with predictive insights
- **API Gateway Expansion**: Support for 10x current throughput
- **Enhanced Security Framework**: Zero-trust architecture implementation
- **AI Integration Layer**: Machine learning-powered automation
- **Scalability Package**: Auto-scaling infrastructure for 500% growth capacity

### Implementation Timeline
- Phase 1 (Weeks 1-4): Infrastructure setup and integration
- Phase 2 (Weeks 5-8): Feature deployment and testing
- Phase 3 (Weeks 9-12): Training and optimization
"""
		return section.strip()

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
	)
	async def create_pricing_section(metrics: str) -> str:
		"""Create the pricing and ROI section."""
		await asyncio.sleep(0.3)

		section = f"""
## INVESTMENT & ROI ANALYSIS

### Pricing Structure
- **Enterprise Plus Package**: $8,500/month
- **Premium Support SLA**: Included (normally $2,000/month)
- **Implementation Services**: $15,000 (one-time)
- **Training & Enablement**: Included

### 12-Month Financial Impact
Based on your current metrics:
{metrics}

**Projected Additional Value:**
- Cost Savings: $185,000/year
- Revenue Enablement: $320,000/year
- Efficiency Gains: $95,000/year
- **Total Value**: $600,000/year

**Net ROI**: 410% in Year 1

### Payment Terms
- Flexible monthly or annual billing
- 30-day money-back guarantee
- Volume discounts available for multi-year commitments
"""
		return section.strip()

	# ==================== Handoff Tools ====================

	transfer_to_data_analyst = agents_manager.execution_wrappers.create_task_description_handoff_tool(
		agent_name="data_analyst",
		description="Assign task to the Data Analysis Agent. Use this to fetch customer history, analyze performance metrics, and retrieve quantitative data.",
	)

	transfer_to_market_researcher = agents_manager.execution_wrappers.create_task_description_handoff_tool(
		agent_name="market_researcher",
		description="Assign task to the Market Research Agent. Use this to research industry trends, analyze competitors, and identify market positioning.",
	)

	transfer_to_document_creator = agents_manager.execution_wrappers.create_task_description_handoff_tool(
		agent_name="document_creator",
		description="Assign task to the Document Creation Agent. Use this to assemble proposal sections, create executive summaries, and format the final document.",
	)

	# ==================== Supervisor Agent ====================

	supervisor_agent = create_react_agent(
		model=ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-pro"),
		tools=[
			transfer_to_data_analyst,
			transfer_to_market_researcher,
			transfer_to_document_creator,
		],
		prompt=(
			"You are a Supervisor Agent coordinating a sales proposal generation workflow.\n\n"
			"Your team consists of:\n"
			"- Data Analysis Agent: Retrieves customer history and performance metrics\n"
			"- Market Research Agent: Analyzes industry trends and competitive landscape\n"
			"- Document Creation Agent: Assembles the final proposal document\n\n"
			"WORKFLOW STRATEGY:\n"
			"1. FIRST: Delegate to Data Analysis Agent to gather customer data and metrics\n"
			"2. SECOND: Delegate to Market Research Agent for industry insights and competitive analysis\n"
			"3. THIRD: Delegate to Document Creation Agent to assemble the final proposal\n"
			"4. Transfer to ONE agent at a time and wait for their results\n"
			"5. Once all sections are complete, provide a summary confirming the proposal is ready\n\n"
			"Coordinate the workflow efficiently and ensure all necessary information is gathered before document creation."
		),
		name="supervisor",
	)

	# ==================== Worker Agents ====================

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.EXECUTION_BLOCK
	)
	async def data_analyst_node(state: WorkflowState) -> WorkflowState:
		"""Data Analysis Agent - Fetches customer data and performance metrics."""
		print("📊 Data Analysis Agent: Gathering customer data and metrics...")

		data_analyst = create_react_agent(
			model=ChatLLMProvider(
				provider="OpenRouter", model="google/gemini-2.5-flash"
			),
			tools=[fetch_customer_history, analyze_performance_metrics],
			name="data_analyst",
			prompt=(
				"You are a Data Analysis Agent. Your job is to:\n"
				"1. Fetch customer history data\n"
				"2. Analyze performance metrics and ROI\n"
				"3. Provide comprehensive quantitative insights\n\n"
				"Use the available tools to gather all relevant customer data. "
				"Present the information in a clear, structured format."
			),
		)

		result = await data_analyst.ainvoke(state)

		print("✅ Data Analysis Agent: Analysis complete")
		return {"messages": result["messages"]}

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.EXECUTION_BLOCK
	)
	async def market_researcher_node(state: WorkflowState) -> WorkflowState:
		"""Market Research Agent - Analyzes industry trends and competitive landscape."""
		print("🔍 Market Research Agent: Analyzing market and competitors...")

		market_researcher = create_react_agent(
			model=ChatLLMProvider(
				provider="OpenRouter", model="google/gemini-2.5-flash"
			),
			tools=[research_industry_trends, analyze_competitors],
			name="market_researcher",
			prompt=(
				"You are a Market Research Agent. Your job is to:\n"
				"1. Research relevant industry trends and forecasts\n"
				"2. Analyze the competitive landscape\n"
				"3. Identify our competitive advantages\n\n"
				"Use the available tools to provide comprehensive market insights. "
				"Focus on actionable intelligence that strengthens the sales proposal."
			),
		)

		result = await market_researcher.ainvoke(state)

		print("✅ Market Research Agent: Research complete")
		return {"messages": result["messages"]}

	@agents_manager.execution_wrappers.asyncflow(
		flow_type=AsyncFlowType.EXECUTION_BLOCK
	)
	async def document_creator_node(state: WorkflowState) -> WorkflowState:
		"""Document Creation Agent - Assembles the final proposal document."""
		print("📝 Document Creation Agent: Assembling proposal document...")

		document_creator = create_react_agent(
			model=ChatLLMProvider(
				provider="OpenRouter", model="google/gemini-2.5-flash"
			),
			tools=[
				create_executive_summary,
				create_technical_section,
				create_pricing_section,
			],
			name="document_creator",
			prompt=(
				"You are a Document Creation Agent. Your job is to:\n"
				"1. Review all gathered data from previous agents\n"
				"2. Create executive summary with key highlights\n"
				"3. Build technical solution section\n"
				"4. Develop pricing and ROI analysis\n"
				"5. Assemble a cohesive, professional proposal\n\n"
				"Use the conversation history to extract relevant information from other agents. "
				"Create all sections using the available tools and present a complete proposal."
			),
		)

		result = await document_creator.ainvoke(state)

		print("✅ Document Creation Agent: Proposal document complete")
		return {"messages": result["messages"]}

	# ==================== Build Graph ====================

	# Register nodes for introspection
	agents_manager.agent_introspector._all_nodes = [
		"supervisor",
		"data_analyst",
		"market_researcher",
		"document_creator",
	]

	# Wrap worker nodes with introspection
	data_analyst_wrapped = agents_manager.agent_introspector.introspect_node(
		data_analyst_node, node_name="data_analyst"
	)
	market_researcher_wrapped = agents_manager.agent_introspector.introspect_node(
		market_researcher_node, node_name="market_researcher"
	)
	document_creator_wrapped = agents_manager.agent_introspector.introspect_node(
		document_creator_node, node_name="document_creator"
	)

	workflow = StateGraph(WorkflowState)

	# Add supervisor with destinations
	workflow.add_node(
		"supervisor",
		supervisor_agent,
		destinations=("data_analyst", "market_researcher", "document_creator"),
	)

	# Add worker nodes
	workflow.add_node("data_analyst", data_analyst_wrapped)
	workflow.add_node("market_researcher", market_researcher_wrapped)
	workflow.add_node("document_creator", document_creator_wrapped)

	# Entry point
	workflow.add_edge(START, "supervisor")

	# Workers return to supervisor for coordination
	workflow.add_edge("data_analyst", "supervisor")
	workflow.add_edge("market_researcher", "supervisor")
	workflow.add_edge("document_creator", "supervisor")

	return workflow


# ==================== Main Execution ====================


async def start_app():
	"""Initialize and run the sales proposal generation workflow."""
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		print("🚀 Sales Proposal Generation - Supervisor Pattern")
		print("=" * 80)

		# Build workflow
		workflow = create_sales_proposal_workflow(agents_manager)

		# Compile with memory
		from langgraph.checkpoint.memory import InMemorySaver

		memory = InMemorySaver()
		app = workflow.compile(checkpointer=memory)

		# Initial request
		initial_state = {
			"messages": [
				HumanMessage(
					content=(
						"Generate a comprehensive sales proposal for our customer 'TechCorp Industries'. "
						"They are in the enterprise software category. "
						"I need a complete proposal with customer analysis, market insights, and a professional document."
					)
				)
			],
			"customer_name": "TechCorp Industries",
			"product_category": "Enterprise Software",
		}

		print("\n📋 Request:")
		print("Generate sales proposal for TechCorp Industries (Enterprise Software)")
		print("=" * 80)

		try:
			# Execute workflow
			config = {
				"configurable": {"thread_id": "sales_proposal_demo"},
				"recursion_limit": 50,
			}

			# Stream execution
			async for chunk in app.astream(
				initial_state, config=config, stream_mode="values"
			):
				# Show latest message if it's from an agent
				if chunk.get("messages"):
					latest_msg = chunk["messages"][-1]
					if hasattr(latest_msg, "content") and latest_msg.content:
						print(
							f"\n💬 {getattr(latest_msg, 'name', 'Agent')}: {latest_msg.content[:200]}..."
						)

			print("\n" + "=" * 80)
			print("✅ Sales Proposal Generation Completed!")
			print("\n📄 The complete proposal has been assembled with:")
			print("   • Customer history and performance analysis")
			print("   • Industry trends and competitive insights")
			print("   • Executive summary, technical specs, and pricing")

		except Exception as e:
			print(f"\n❌ Workflow failed: {str(e)}")
			raise
		finally:
			# Generate telemetry
			current_directory = str(pathlib.Path(__file__).parent.resolve())
			agents_manager.utils.create_output_results_dirs(current_directory)

			agents_manager.agent_introspector.generate_report(
				dir_to_write=current_directory
			)
			await agents_manager.utils.render_graph(app, dir_to_write=current_directory)
			print("\n📊 Execution report and graph visualization generated!")


if __name__ == "__main__":
	asyncio.run(start_app())
