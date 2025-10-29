"""
Tools and tasks for sales analytics workflow.
3 agent types: Data Extraction (MCP), Analytics, Report Generation
Plus deterministic context prep tasks.
"""

from flowgentic.langGraph.execution_wrappers import AsyncFlowType
import asyncio
from typing import Dict, Any, List
from pathlib import Path
from ...utils.schemas import (
	QueryValidation,
	AgentOutput,
	AnalysisContext,
	SalesMetrics,
	AnalysisType,
)


class DatabaseTools:
	"""MCP SQLite database tools."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tools = {}

	def register_tools(self):
		"""Register MCP database specialist tool."""

		# Get database path (relative to this file)
		db_path = Path(__file__).parent.parent.parent / "sales.db"
		db_path = db_path.resolve()

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_MCP,
			tool_description="Database specialist for querying sales data via SQL using SQLite MCP Toolbox server",
			mcp_servers={
				"sqlite": {
					"command": "uvx",
					"args": ["mcp-sqlite", str(db_path)],
					"transport": "stdio",
				}
			},
		)
		async def database_specialist(question: str):
			"""
			SQLite database specialist with access to sales, products, and sales_reps tables.
			Can execute SQL queries and retrieve comprehensive sales data.
			"""
			pass

		self.tools = {
			"database_specialist": database_specialist,
		}
		return self.tools


class AnalyticsTools:
	"""Statistical analysis and metrics tools."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tools = {}

	def register_tools(self):
		"""Register analytics agent tools."""

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def calculate_growth_rate(
			current_value: float, previous_value: float
		) -> Dict[str, Any]:
			"""Calculate growth rate between two periods."""
			await asyncio.sleep(0.3)

			if previous_value == 0:
				return {"growth_rate": 0, "note": "No previous data"}

			growth = ((current_value - previous_value) / previous_value) * 100

			return {
				"growth_rate": round(growth, 2),
				"current_value": current_value,
				"previous_value": previous_value,
				"absolute_change": round(current_value - previous_value, 2),
				"trend": "increasing" if growth > 0 else "decreasing",
			}

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def detect_anomalies(data_points: List[float]) -> Dict[str, Any]:
			"""Detect anomalies in sales data using statistical methods."""
			await asyncio.sleep(0.4)

			if len(data_points) < 3:
				return {"anomalies": [], "note": "Insufficient data"}

			# IQR method for anomaly detection
			sorted_data = sorted(data_points)
			q1_idx = len(sorted_data) // 4
			q3_idx = 3 * len(sorted_data) // 4

			q1 = sorted_data[q1_idx]
			q3 = sorted_data[q3_idx]
			iqr = q3 - q1

			lower_bound = q1 - 1.5 * iqr
			upper_bound = q3 + 1.5 * iqr

			anomalies = [x for x in data_points if x < lower_bound or x > upper_bound]

			return {
				"anomalies_count": len(anomalies),
				"anomalies": anomalies[:5],  # Show first 5
				"threshold_lower": round(lower_bound, 2),
				"threshold_upper": round(upper_bound, 2),
				"assessment": "normal" if len(anomalies) == 0 else "anomalies_detected",
			}

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def calculate_performance_metrics(sales_data: str) -> Dict[str, Any]:
			"""Calculate key sales performance metrics."""
			await asyncio.sleep(0.5)

			# Mock metrics based on data analysis
			return {
				"average_deal_size": 12500.50,
				"conversion_rate": 23.5,
				"customer_lifetime_value": 45000,
				"sales_cycle_days": 45,
				"win_rate": 28.5,
				"revenue_per_rep": 125000,
				"top_performers": ["Alice Johnson", "Carol White"],
			}

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def trend_analysis(time_series_data: List[float]) -> Dict[str, Any]:
			"""Analyze trends in time series sales data."""
			await asyncio.sleep(0.4)

			if len(time_series_data) < 2:
				return {"trend": "insufficient_data"}

			# Simple trend analysis
			first_half = sum(time_series_data[: len(time_series_data) // 2])
			second_half = sum(time_series_data[len(time_series_data) // 2 :])

			trend = "increasing" if second_half > first_half else "decreasing"

			return {
				"trend": trend,
				"strength": "strong"
				if abs(second_half - first_half) / first_half > 0.2
				else "moderate",
				"forecast_direction": trend,
				"confidence": 0.85,
			}

		self.tools = {
			"calculate_growth_rate": calculate_growth_rate,
			"detect_anomalies": detect_anomalies,
			"calculate_performance_metrics": calculate_performance_metrics,
			"trend_analysis": trend_analysis,
		}
		return self.tools


class ReportTools:
	"""Report generation tools."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tools = {}

	def register_tools(self):
		"""Register report generation tools."""

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def generate_executive_summary(analysis: Dict[str, Any]) -> str:
			"""Generate executive summary from analysis."""
			await asyncio.sleep(0.4)

			summary = f"""
EXECUTIVE SUMMARY
=================
Total Revenue: ${analysis.get("total_revenue", 0):,.2f}
Growth Rate: {analysis.get("growth_rate", 0)}%
Performance Tier: {analysis.get("performance", "Strong")}

Key Findings:
- Revenue trending {analysis.get("trend", "upward")}
- Top region: {analysis.get("top_region", "North")}
- {analysis.get("anomaly_count", 0)} anomalies detected

Recommendations:
- Maintain current growth trajectory
- Focus resources on high-performing regions
- Address identified performance gaps
"""
			return summary.strip()

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def create_visualizations(data: Dict[str, Any]) -> str:
			"""Create data visualizations description."""
			await asyncio.sleep(0.3)

			viz_list = [
				"Revenue Trend Line Chart (12 months)",
				"Regional Performance Bar Chart",
				"Product Category Pie Chart",
				"Sales Rep Leaderboard",
			]

			return f"Generated {len(viz_list)} visualizations:\n" + "\n".join(
				f"  - {v}" for v in viz_list
			)

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def format_recommendations(insights: List[str]) -> str:
			"""Format actionable recommendations."""
			await asyncio.sleep(0.2)

			formatted = "ACTIONABLE RECOMMENDATIONS:\n"
			for i, insight in enumerate(insights[:5], 1):
				formatted += f"\n{i}. {insight}"

			return formatted

		self.tools = {
			"generate_executive_summary": generate_executive_summary,
			"create_visualizations": create_visualizations,
			"format_recommendations": format_recommendations,
		}
		return self.tools


class ValidationTasks:
	"""Query validation tasks."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tasks = {}

	def register_function_tasks(self):
		"""Register validation tasks."""

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def validate_query_task(user_query: str) -> QueryValidation:
			"""Validate and parse sales analytics query - deterministic operation."""

			words = user_query.split()

			# Detect analysis type
			analysis_type = None
			if any(
				word in user_query.lower() for word in ["revenue", "sales", "income"]
			):
				analysis_type = AnalysisType.REVENUE
			elif any(
				word in user_query.lower() for word in ["performance", "rep", "team"]
			):
				analysis_type = AnalysisType.PERFORMANCE
			elif any(
				word in user_query.lower() for word in ["trend", "pattern", "forecast"]
			):
				analysis_type = AnalysisType.TRENDS

			# Detect regions
			regions = []
			region_keywords = ["north", "south", "east", "west"]
			for keyword in region_keywords:
				if keyword in user_query.lower():
					regions.append(keyword.capitalize())

			# Detect time period
			time_period = "last_year"
			if "quarter" in user_query.lower():
				time_period = "quarterly"
			elif "month" in user_query.lower():
				time_period = "monthly"
			elif "week" in user_query.lower():
				time_period = "weekly"

			validation = QueryValidation(
				is_valid=len(user_query.strip()) > 10,
				cleaned_query=user_query.strip(),
				word_count=len(words),
				timestamp=asyncio.get_event_loop().time(),
				analysis_type=analysis_type,
				time_period=time_period,
				regions=regions,
				metadata={
					"has_sales_keywords": any(
						word in user_query.lower()
						for word in ["sales", "revenue", "performance", "analytics"]
					),
					"complexity_score": min(len(words) / 15, 1.0),
					"requires_multi_stage": True,
				},
			)

			return validation

		self.tasks = {
			"validate_query": validate_query_task,
		}
		return self.tasks


class ContextTasks:
	"""Context preparation tasks between stages."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tasks = {}

	def register_function_tasks(self):
		"""Register context management tasks."""

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def prepare_analytics_context_task(
			data_output: AgentOutput, validation: QueryValidation
		) -> AnalysisContext:
			"""Prepare context for analytics stage - deterministic operation."""

			context = AnalysisContext(
				database_results=data_output.output_content,
				query_parameters=validation,
				processing_stage="analytics_prep",
				stage_sequence=1,
				additional_context={
					"data_extraction_time": data_output.execution_time,
					"data_extraction_tools": data_output.tools_used,
					"query_type": validation.analysis_type.value
					if validation.analysis_type
					else "general",
					"regions_focus": validation.regions,
				},
			)

			return context

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def prepare_report_context_task(
			analytics_output: AgentOutput, existing_context: AnalysisContext
		) -> AnalysisContext:
			"""Prepare context for report generation - deterministic operation."""

			# Update context for next stage
			existing_context.processing_stage = "report_generation_prep"
			existing_context.stage_sequence = 2
			existing_context.additional_context.update(
				{
					"analytics_complete": True,
					"analytics_time": analytics_output.execution_time,
					"analytics_tools_used": analytics_output.tools_used,
					"insights_generated": True,
				}
			)

			# Parse metrics from analytics output if available
			if "metrics" in analytics_output.structured_data:
				existing_context.sales_metrics = analytics_output.structured_data[
					"metrics"
				]

			return existing_context

		self.tasks = {
			"prepare_analytics_context": prepare_analytics_context_task,
			"prepare_report_context": prepare_report_context_task,
		}
		return self.tasks


class FormattingTasks:
	"""Report formatting tasks."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tasks = {}

	def register_function_tasks(self):
		"""Register formatting tasks."""

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def format_final_report_task(
			report_output: AgentOutput,
			analytics_output: AgentOutput,
			data_output: AgentOutput,
			context: AnalysisContext,
		) -> str:
			"""Format the final sales analytics report - deterministic operation."""

			formatted_report = f"""
╔══════════════════════════════════════════════════════════════════════════╗
║                    SALES ANALYTICS REPORT                                ║
╚══════════════════════════════════════════════════════════════════════════╝

📊 QUERY DETAILS
───────────────────────────────────────────────────────────────────────────
Analysis Type: {context.query_parameters.analysis_type.value.upper() if context.query_parameters.analysis_type else "GENERAL"}
Time Period: {context.query_parameters.time_period}
Regions: {", ".join(context.query_parameters.regions) if context.query_parameters.regions else "All Regions"}
Query: {context.query_parameters.cleaned_query[:100]}...

🔍 DATA EXTRACTION (MCP SQLite Toolbox)
───────────────────────────────────────────────────────────────────────────
Agent: {data_output.agent_name}
Execution Time: {data_output.execution_time:.2f}s
Tools Used: SQLite MCP Database Specialist

{data_output.output_content[:500]}...

📈 ANALYTICS & INSIGHTS
───────────────────────────────────────────────────────────────────────────
Agent: {analytics_output.agent_name}
Execution Time: {analytics_output.execution_time:.2f}s
Tools Used: {", ".join(analytics_output.tools_used)}

{analytics_output.output_content[:500]}...

📄 RECOMMENDATIONS & REPORT
───────────────────────────────────────────────────────────────────────────
Agent: {report_output.agent_name}
Execution Time: {report_output.execution_time:.2f}s
Tools Used: {", ".join(report_output.tools_used)}

{report_output.output_content}

═══════════════════════════════════════════════════════════════════════════
WORKFLOW SUMMARY
───────────────────────────────────────────────────────────────────────────
Total Processing Time: {data_output.execution_time + analytics_output.execution_time + report_output.execution_time:.2f}s
Pipeline: Data Extraction (MCP) → Analytics → Report Generation
Agents Deployed: 3
Tools Invoked: {len(set(data_output.tools_used + analytics_output.tools_used + report_output.tools_used))}
Status: ✅ Complete

Generated by FlowGentic Sales Analytics Pipeline (MCP Integration)
═══════════════════════════════════════════════════════════════════════════
"""
			return formatted_report.strip()

		self.tasks = {
			"format_final_report": format_final_report_task,
		}
		return self.tasks
