from flowgentic.langGraph.agents import AsyncFlowType
import asyncio
import random
from typing import Dict, Any, List
from ...utils.schemas import (
	InvestmentValidation,
	AgentOutput,
	PortfolioContext,
	WorkflowState,
	MarketData,
	RiskMetrics,
	RiskLevel,
)


class MarketResearchTools:
	"""Market research and financial data tools."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tools = {}

	def register_tools(self):
		"""Register market research agent tools."""

		@self.agents_manager.agents.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def get_market_data_tool(ticker: str) -> Dict[str, Any]:
			"""Fetch comprehensive market data for a given stock ticker."""
			await asyncio.sleep(0.8)  # Simulate API call

			# Mock realistic market data
			sectors = ["Technology", "Healthcare", "Finance", "Energy", "Consumer"]
			ratings = ["Strong Buy", "Buy", "Hold", "Underperform", "Sell"]

			market_data = {
				"ticker": ticker.upper(),
				"current_price": round(random.uniform(50, 500), 2),
				"market_cap": round(random.uniform(10, 500), 2),  # in billions
				"pe_ratio": round(random.uniform(10, 35), 2),
				"dividend_yield": round(random.uniform(0, 5), 2),
				"volatility": round(random.uniform(15, 45), 2),
				"sector": random.choice(sectors),
				"analyst_rating": random.choice(ratings),
				"52_week_high": round(random.uniform(100, 600), 2),
				"52_week_low": round(random.uniform(30, 100), 2),
				"volume": random.randint(1000000, 50000000),
				"news_sentiment": round(random.uniform(-1, 1), 2),
			}
			return market_data

		@self.agents_manager.agents.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def analyze_sector_trends_tool(sector: str) -> Dict[str, Any]:
			"""Analyze trends and outlook for a specific market sector."""
			await asyncio.sleep(0.6)

			trends = {
				"sector": sector,
				"growth_rate": round(random.uniform(-5, 25), 2),
				"outlook": random.choice(["Bullish", "Neutral", "Bearish"]),
				"key_drivers": [
					"Technological innovation",
					"Regulatory changes",
					"Market demand shifts",
				],
				"top_performers": [f"{sector[:3].upper()}{i}" for i in range(1, 4)],
				"risks": ["Market volatility", "Economic headwinds", "Competition"],
				"analyst_consensus": random.choice(["Positive", "Mixed", "Negative"]),
			}
			return trends

		@self.agents_manager.agents.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def economic_indicators_tool() -> Dict[str, Any]:
			"""Fetch current economic indicators and macro trends."""
			await asyncio.sleep(0.5)

			indicators = {
				"gdp_growth": round(random.uniform(1.5, 4.5), 2),
				"inflation_rate": round(random.uniform(1.0, 6.0), 2),
				"unemployment_rate": round(random.uniform(3.5, 7.0), 2),
				"interest_rate": round(random.uniform(0.5, 5.5), 2),
				"consumer_confidence": round(random.uniform(80, 130), 1),
				"manufacturing_pmi": round(random.uniform(45, 65), 1),
				"market_sentiment": random.choice(["Risk-on", "Risk-off", "Neutral"]),
				"outlook": "Economic data suggests moderate growth with controlled inflation",
			}
			return indicators

		self.tools = {
			"get_market_data": get_market_data_tool,
			"analyze_sector_trends": analyze_sector_trends_tool,
			"economic_indicators": economic_indicators_tool,
		}
		return self.tools


class RiskAnalysisTools:
	"""Risk assessment and compliance tools."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tools = {}

	def register_tools(self):
		"""Register risk analysis agent tools."""

		@self.agents_manager.agents.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def calculate_portfolio_risk_tool(
			holdings: List[Dict[str, Any]],
		) -> Dict[str, Any]:
			"""Calculate comprehensive risk metrics for a portfolio."""
			await asyncio.sleep(0.7)

			risk_analysis = {
				"portfolio_variance": round(random.uniform(0.15, 0.45), 4),
				"sharpe_ratio": round(random.uniform(0.8, 2.5), 2),
				"max_drawdown": round(random.uniform(10, 35), 2),
				"beta": round(random.uniform(0.7, 1.4), 2),
				"value_at_risk_95": round(random.uniform(5, 20), 2),
				"diversification_score": round(random.uniform(60, 95), 1),
				"concentration_risk": random.choice(["Low", "Moderate", "High"]),
				"correlation_matrix": "Computed for portfolio assets",
			}
			return risk_analysis

		@self.agents_manager.agents.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def regulatory_compliance_check_tool(
			portfolio: Dict[str, Any],
		) -> Dict[str, Any]:
			"""Check portfolio against regulatory requirements and constraints."""
			await asyncio.sleep(0.4)

			compliance = {
				"status": random.choice(
					["Compliant", "Compliant with warnings", "Non-compliant"]
				),
				"fiduciary_standard": "Met",
				"concentration_limits": "Within bounds",
				"suitability_check": "Passed",
				"flags": []
				if random.random() > 0.3
				else ["High concentration in single sector"],
				"recommendations": [
					"Increase diversification across asset classes",
					"Consider rebalancing quarterly",
				],
			}
			return compliance

		self.tools = {
			"calculate_portfolio_risk": calculate_portfolio_risk_tool,
			"regulatory_compliance_check": regulatory_compliance_check_tool,
		}
		return self.tools


class PortfolioStrategyTools:
	"""Portfolio construction and strategy tools."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tools = {}

	def register_tools(self):
		"""Register portfolio strategy agent tools."""

		@self.agents_manager.agents.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def optimize_portfolio_tool(
			constraints: Dict[str, Any],
		) -> Dict[str, Any]:
			"""Generate optimal portfolio allocation using modern portfolio theory."""
			await asyncio.sleep(0.9)

			# Mock portfolio allocation
			allocation = {
				"strategy": "Diversified Growth",
				"allocations": {
					"Equities": 60,
					"Bonds": 25,
					"Real Estate": 10,
					"Cash": 5,
				},
				"expected_return": round(random.uniform(7, 14), 2),
				"expected_risk": round(random.uniform(10, 20), 2),
				"optimization_method": "Mean-Variance Optimization",
				"rebalancing_frequency": "Quarterly",
				"tax_efficiency_score": round(random.uniform(70, 95), 1),
			}
			return allocation

		@self.agents_manager.agents.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def generate_recommendations_tool(analysis: Dict[str, Any]) -> List[str]:
			"""Generate actionable investment recommendations."""
			await asyncio.sleep(0.5)

			recommendations = [
				"Allocate 60% to diversified equity index funds for long-term growth",
				"Maintain 25% in investment-grade bonds to reduce volatility",
				"Consider 10% in REIT exposure for inflation protection",
				"Keep 5% in cash equivalents for liquidity and opportunities",
				"Review and rebalance portfolio quarterly to maintain target allocation",
				"Implement dollar-cost averaging for new contributions",
				"Consider tax-loss harvesting strategies in taxable accounts",
			]
			return random.sample(recommendations, k=random.randint(4, 6))

		@self.agents_manager.agents.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def generate_report_document_tool(portfolio_data: Dict[str, Any]) -> str:
			"""Generate a formatted investment report document."""
			await asyncio.sleep(0.6)

			report = f"""
			INVESTMENT PORTFOLIO ANALYSIS REPORT
			=====================================
			
			Portfolio Strategy: {portfolio_data.get("strategy", "Balanced Growth")}
			Expected Annual Return: {portfolio_data.get("expected_return", 10)}%
			Expected Risk (Std Dev): {portfolio_data.get("expected_risk", 15)}%
			
			Key Findings:
			- Portfolio demonstrates strong diversification
			- Risk-adjusted returns are competitive with benchmarks
			- Tax efficiency optimized for long-term wealth building
			
			This report was generated using comprehensive market analysis and risk assessment.
			"""
			return report.strip()

		self.tools = {
			"optimize_portfolio": optimize_portfolio_tool,
			"generate_recommendations": generate_recommendations_tool,
			"generate_report_document": generate_report_document_tool,
		}
		return self.tools


class ValidationTasks:
	"""Input validation and preprocessing tasks."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tasks = {}

	def register_function_tasks(self):
		"""Register validation tasks."""

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)
		async def validate_investment_query_task(
			user_input: str,
		) -> InvestmentValidation:
			"""Validate and parse investment query - deterministic operation."""

			# Extract potential tickers (simple pattern matching)
			words = user_input.split()
			tickers = [w.upper() for w in words if len(w) <= 5 and w.isupper()]

			# Detect investment amount
			amount = None
			for word in words:
				if "$" in word or "k" in word.lower():
					try:
						amount = float(
							word.replace("$", "").replace("k", "").replace(",", "")
						) * (1000 if "k" in word.lower() else 1)
					except:
						pass

			# Detect risk tolerance
			risk_keywords = {
				"conservative": RiskLevel.LOW,
				"moderate": RiskLevel.MODERATE,
				"aggressive": RiskLevel.HIGH,
				"high-risk": RiskLevel.VERY_HIGH,
			}
			risk_tolerance = None
			for keyword, level in risk_keywords.items():
				if keyword in user_input.lower():
					risk_tolerance = level
					break

			validation_result = InvestmentValidation(
				is_valid=len(user_input.strip()) > 10,
				cleaned_input=user_input.strip(),
				word_count=len(words),
				timestamp=asyncio.get_event_loop().time(),
				detected_tickers=tickers,
				investment_amount=amount,
				time_horizon="long-term"
				if "long" in user_input.lower()
				else "medium-term",
				risk_tolerance=risk_tolerance or RiskLevel.MODERATE,
				metadata={
					"has_portfolio_keywords": any(
						word in user_input.lower()
						for word in ["portfolio", "invest", "allocation", "diversify"]
					),
					"complexity_score": min(len(words) / 20, 1.0),
					"domain": "investment_advisory",
				},
			)
			return validation_result

		self.tasks = {
			"validate_investment_query": validate_investment_query_task,
		}
		return self.tasks


class ContextTasks:
	"""Context preparation and management tasks."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tasks = {}

	def register_function_tasks(self):
		"""Register context management tasks."""

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)
		async def prepare_risk_context_task(
			market_output: AgentOutput, validation_data: InvestmentValidation
		) -> PortfolioContext:
			"""Prepare context for risk assessment - deterministic operation."""

			context = PortfolioContext(
				market_analysis=market_output.output_content,
				investment_parameters=validation_data,
				processing_stage="risk_assessment_prep",
				agent_sequence=1,
				portfolio_constraints={
					"max_single_position": 15,
					"min_diversification": 8,
					"risk_tolerance": validation_data.risk_tolerance,
				},
				additional_context={
					"market_research_tools": market_output.tools_used,
					"execution_time": market_output.execution_time,
					"tickers_analyzed": validation_data.detected_tickers,
				},
			)
			return context

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)
		async def prepare_strategy_context_task(
			risk_output: AgentOutput, existing_context: PortfolioContext
		) -> PortfolioContext:
			"""Prepare context for portfolio strategy - deterministic operation."""

			existing_context.processing_stage = "portfolio_strategy_prep"
			existing_context.agent_sequence = 2
			existing_context.additional_context.update(
				{
					"risk_assessment_complete": True,
					"risk_tools_used": risk_output.tools_used,
					"risk_execution_time": risk_output.execution_time,
				}
			)

			# Parse risk metrics from structured data
			if "risk_metrics" in risk_output.structured_data:
				existing_context.risk_metrics = risk_output.structured_data[
					"risk_metrics"
				]

			return existing_context

		self.tasks = {
			"prepare_risk_context": prepare_risk_context_task,
			"prepare_strategy_context": prepare_strategy_context_task,
		}
		return self.tasks


class FormattingTasks:
	"""Output formatting and finalization tasks."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager
		self.tasks = {}

	def register_function_tasks(self):
		"""Register formatting tasks."""

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)
		async def format_investment_report_task(
			strategy_output: AgentOutput,
			risk_output: AgentOutput,
			market_output: AgentOutput,
			context: PortfolioContext,
		) -> str:
			"""Format the final investment advisory report."""

			formatted_report = f"""
╔══════════════════════════════════════════════════════════════════════════╗
║           COMPREHENSIVE INVESTMENT ADVISORY REPORT                       ║
╚══════════════════════════════════════════════════════════════════════════╝

📊 CLIENT PROFILE
───────────────────────────────────────────────────────────────────────────
Investment Amount: ${context.investment_parameters.investment_amount or "Not specified"}
Risk Tolerance: {context.investment_parameters.risk_tolerance.value.upper()}
Time Horizon: {context.investment_parameters.time_horizon}
Query: {context.investment_parameters.cleaned_input[:100]}...

🔍 MARKET RESEARCH ANALYSIS
───────────────────────────────────────────────────────────────────────────
Agent: {market_output.agent_name}
Execution Time: {market_output.execution_time:.2f}s
Tools Used: {", ".join(market_output.tools_used)}

{market_output.output_content[:500]}...

⚠️  RISK ASSESSMENT
───────────────────────────────────────────────────────────────────────────
Agent: {risk_output.agent_name}
Execution Time: {risk_output.execution_time:.2f}s
Tools Used: {", ".join(risk_output.tools_used)}

{risk_output.output_content[:500]}...

📈 PORTFOLIO STRATEGY & RECOMMENDATIONS
───────────────────────────────────────────────────────────────────────────
Agent: {strategy_output.agent_name}
Execution Time: {strategy_output.execution_time:.2f}s
Tools Used: {", ".join(strategy_output.tools_used)}

{strategy_output.output_content}

═══════════════════════════════════════════════════════════════════════════
WORKFLOW SUMMARY
───────────────────────────────────────────────────────────────────────────
Total Processing Time: {market_output.execution_time + risk_output.execution_time + strategy_output.execution_time:.2f}s
Agents Deployed: 3 (Market Research → Risk Assessment → Portfolio Strategy)
Tools Invoked: {len(set(market_output.tools_used + risk_output.tools_used + strategy_output.tools_used))}
Status: ✅ Complete

DISCLAIMER: This report is generated for educational purposes. Consult a 
licensed financial advisor before making investment decisions.
═══════════════════════════════════════════════════════════════════════════
			"""
			return formatted_report.strip()

		self.tasks = {
			"format_investment_report": format_investment_report_task,
		}
		return self.tasks
