"""
Product Research Assistant - Extended Supervisor Pattern

This example demonstrates an advanced supervisor pattern with:
- Parallel LLM-based worker agents
- Conditional synthesis based on audience context
- Two different synthesizer strategies

Flow:
  START → router → [tech_agent || reviews_agent] → gather → synthesis_router
        → [technical_synthesizer OR consumer_synthesizer] → END
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
from typing import Annotated, Dict, List, Optional
import logging
import time
import operator

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field
from radical.asyncflow import ConcurrentExecutionBackend

from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.utils.supervisor import create_llm_router, supervisor_fan_out
from flowgentic.utils.llm_providers import ChatLLMProvider

import logging

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


class GraphState(BaseModel):
	"""State schema for product research supervisor workflow."""

	query: str = Field(..., description="User's product research query")
	product_name: Optional[str] = Field(
		default=None, description="Extracted product name"
	)
	audience_type: Optional[str] = Field(
		default=None, description="Target audience: 'technical' or 'consumer'"
	)
	routing_decision: Optional[List[str]] = Field(
		default=None, description="List of worker agents to route to"
	)
	routing_rationale: Optional[str] = Field(
		default=None, description="Explanation for routing decision"
	)
	results: Annotated[Dict[str, str], operator.or_] = Field(
		default_factory=dict, description="Results from parallel worker agents"
	)
	gathered_data: Optional[str] = Field(
		default=None, description="Raw combined data from agents before synthesis"
	)
	synthesis_decision: Optional[str] = Field(
		default=None, description="Which synthesizer to use: 'technical' or 'consumer'"
	)
	final_report: Optional[str] = Field(
		default=None, description="Final synthesized research report"
	)
	messages: Annotated[List[BaseMessage], add_messages] = []


async def main():
	logging.basicConfig(
		level=logging.INFO,
		format="%(asctime)s.%(msecs)03d %(threadName)s %(levelname)s: %(message)s",
		datefmt="%H:%M:%S",
	)

	graph = StateGraph(GraphState)
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		# ================================================================
		# STEP 0: Register agents tools
		# ================================================================

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def search_product_specifications(product_name: str) -> str:
			"""
			Search for technical specifications of a product.

			Args:
			      product_name: Name of the product to search for

			Returns:
			      Mock technical specifications data
			"""
			# Mock data - in production, this would call a real API
			mock_specs = {
				"iphone": """
                        iPhone 15 Pro Specifications:
                        - Processor: A17 Pro (3nm) - 6-core CPU, 6-core GPU
                        - Display: 6.1" OLED, 2556x1179, 120Hz ProMotion, 2000 nits peak
                        - RAM: 8GB LPDDR5
                        - Storage: 128GB/256GB/512GB/1TB NVMe
                        - Camera: 48MP main + 12MP ultra-wide + 12MP telephoto (3x optical)
                        - Battery: 3,274 mAh, 23h video playback
                        - Build: Titanium frame, Ceramic Shield glass
                        - Connectivity: 5G, Wi-Fi 6E, Bluetooth 5.3, USB-C
                        - Weight: 187g
                        """,
				"samsung galaxy": """
                        Samsung Galaxy S24 Specifications:
                        - Processor: Snapdragon 8 Gen 3 - 8-core CPU, Adreno 750 GPU
                        - Display: 6.2" AMOLED, 2340x1080, 120Hz, 2600 nits peak
                        - RAM: 8GB LPDDR5X
                        - Storage: 128GB/256GB UFS 4.0
                        - Camera: 50MP main + 12MP ultra-wide + 10MP telephoto (3x)
                        - Battery: 4,000 mAh, 29h video playback
                        - Build: Aluminum frame, Gorilla Glass Victus 2
                        - Connectivity: 5G, Wi-Fi 6E, Bluetooth 5.3, USB-C
                        - Weight: 167g
                        """,
				"macbook": """
                        MacBook Pro M3 Specifications:
                        - Processor: Apple M3 - 8-core CPU, 10-core GPU, 16-core Neural Engine
                        - Display: 14.2" Liquid Retina XDR, 3024x1964, 120Hz, 1000 nits sustained
                        - RAM: 8GB/16GB/24GB unified memory
                        - Storage: 512GB/1TB/2TB/4TB SSD
                        - Battery: 70Wh, up to 22h video playback
                        - Ports: 3x Thunderbolt 4, HDMI, SD card, MagSafe 3
                        - Build: Aluminum unibody
                        - Weight: 1.55kg
                        """,
			}

			for key, specs in mock_specs.items():
				if key.lower() in product_name.lower():
					return specs

			return f"Mock specifications for {product_name} (Generic): CPU: High-performance, Display: Premium, Storage: 256GB+"

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def search_user_reviews(product_name: str) -> str:
			"""
			Search for user reviews and ratings of a product.

			Args:
			      product_name: Name of the product to search reviews for

			Returns:
			      Mock user review data
			"""
			# Mock data - in production, this would scrape review sites
			mock_reviews = {
				"iphone": """
                        iPhone 15 Pro User Reviews (4.6/5 stars, 12,450 reviews):
                        
                        Top Praises:
                        - "Camera quality is absolutely stunning, especially in low light" (mentioned in 68% of reviews)
                        - "The titanium design feels premium and lighter than previous models" (61%)
                        - "A17 Pro chip handles intensive gaming and video editing flawlessly" (54%)
                        - "USB-C finally! Game changer for connectivity" (47%)
                        
                        Common Complaints:
                        - "Battery life could be better, barely makes it through a full day" (31% of reviews)
                        - "Price is extremely high for incremental upgrades" (28%)
                        - "Gets warm during intensive tasks like 4K video recording" (19%)
                        - "128GB base storage is inadequate in 2024" (15%)
                        
                        Value Assessment: Mixed - Professional users love it, casual users find it overpriced
                        """,
				"samsung galaxy": """
                        Samsung Galaxy S24 User Reviews (4.5/5 stars, 8,920 reviews):
                        
                        Top Praises:
                        - "Best Android phone for the price, great value" (mentioned in 72% of reviews)
                        - "Screen is gorgeous, brightest I've seen on any phone" (65%)
                        - "AI features are genuinely useful, not gimmicky" (58%)
                        - "Compact size perfect for one-handed use" (51%)
                        
                        Common Complaints:
                        - "Battery life disappointing compared to S23 Ultra" (38% of reviews)
                        - "Camera quality good but not class-leading" (27%)
                        - "Base storage only 128GB with no SD card slot" (24%)
                        - "One UI still has bloatware" (18%)
                        
                        Value Assessment: Excellent - Best flagship value in Android market
                        """,
				"macbook": """
                        MacBook Pro M3 User Reviews (4.7/5 stars, 6,340 reviews):
                        
                        Top Praises:
                        - "M3 chip is incredibly fast, compiles code in seconds" (mentioned in 74% of reviews)
                        - "Battery life is exceptional, 15+ hours real-world use" (69%)
                        - "Display quality unmatched, perfect for photo/video work" (63%)
                        - "Build quality and trackpad are industry-leading" (58%)
                        
                        Common Complaints:
                        - "Base 8GB RAM insufficient for professional workflows" (42% of reviews)
                        - "Price-to-spec ratio poor compared to PC alternatives" (35%)
                        - "Limited ports, need dongles for everything" (29%)
                        - "No Face ID, still using Touch ID" (12%)
                        
                        Value Assessment: Good for professionals, expensive for general users
                        """,
			}

			for key, reviews in mock_reviews.items():
				if key.lower() in product_name.lower():
					return reviews

			return f"Mock reviews for {product_name}: 4.3/5 stars - Generally positive feedback with some concerns about price."

		# ================================================================
		# STEP 1: Initial Router - Decides which worker agents to call
		# ================================================================

		# Define available agents and their responsibilities
		agents_responsibilities = """
            Available agents:
            - technical_specs_agent: Analyzes technical specifications, features, performance metrics, build quality
            - user_reviews_agent: Analyzes user reviews, ratings, sentiment, common complaints and praises
            """

		router_model = ChatLLMProvider(
			provider="OpenRouter", model="google/gemini-2.5-pro"
		)

		llm_router = agents_manager.execution_wrappers.asyncflow(
			create_llm_router(agents_responsibilities, router_model),
			flow_type=AsyncFlowType.EXECUTION_BLOCK,
		)

		# ================================================================
		# STEP 2: Worker Agents - Actual LLM agents running in parallel
		# ================================================================

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def technical_specs_agent(state: GraphState) -> dict:
			"""LLM agent that researches technical specifications."""
			start = time.perf_counter()
			logging.info(f"🔧 Technical Specs Agent START - Analyzing: '{state.query}'")

			# Create a ReAct agent for technical analysis with search tools
			agent = create_react_agent(
				model=ChatLLMProvider(
					provider="OpenRouter", model="google/gemini-2.5-flash"
				),
				tools=[search_product_specifications],  # Use mock web search tool
			)

			system_prompt = """You are a technical specifications analyst. 
Your job is to provide detailed technical analysis of products.

You have access to a search tool to find product specifications. Use it to gather technical data.

Focus on:
- Core specifications (CPU, RAM, storage, display, etc.)
- Performance benchmarks and metrics
- Build quality and materials
- Technical comparisons with competitors
- Professional/enterprise features

Be specific, data-driven, and technical. Use industry terminology."""

			result = await agent.ainvoke(
				{
					"messages": [
						SystemMessage(content=system_prompt),
						HumanMessage(
							content=f"Use the search tool to find and analyze the technical specifications for: {state.query}"
						),
					]
				}
			)

			technical_analysis = result["messages"][-1].content.strip()

			elapsed = (time.perf_counter() - start) * 1000
			logging.info(f"🔧 Technical Specs Agent END - took_ms={elapsed:.1f}")

			return {
				"results": {"technical_specs": technical_analysis},
				"messages": result["messages"],  # Include messages in the return dict
			}

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def user_reviews_agent(state: GraphState) -> dict:
			"""LLM agent that analyzes user reviews and sentiment."""
			start = time.perf_counter()
			logging.info(f"⭐ User Reviews Agent START - Analyzing: '{state.query}'")

			# Create a ReAct agent for review analysis with search tools
			agent = create_react_agent(
				model=ChatLLMProvider(
					provider="OpenRouter", model="google/gemini-2.5-flash"
				),
				tools=[search_user_reviews],  # Use mock review search tool
			)

			system_prompt = """You are a user review and sentiment analyst.
Your job is to synthesize user feedback and opinions about products.

You have access to a search tool to find user reviews. Use it to gather review data.

Focus on:
- Overall user satisfaction and ratings
- Common praises (what users love)
- Common complaints (what users dislike)
- Value for money perception
- Real-world usage experiences
- Sentiment trends over time

Be balanced, cite user experiences, and highlight patterns."""

			result = await agent.ainvoke(
				{
					"messages": [
						SystemMessage(content=system_prompt),
						HumanMessage(
							content=f"Use the search tool to find and analyze user reviews and sentiment for: {state.query}"
						),
					]
				}
			)

			reviews_analysis = result["messages"][-1].content.strip()

			elapsed = (time.perf_counter() - start) * 1000
			logging.info(f"⭐ User Reviews Agent END - took_ms={elapsed:.1f}")

			return {
				"results": {"user_reviews": reviews_analysis},
				"messages": result["messages"],  # Include messages in the return dict
			}

		# ================================================================
		# STEP 3: Gather Node - Collects results and extracts context
		# ================================================================

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def gather_results(state: GraphState) -> dict:
			"""Gather results from worker agents and extract audience context."""
			logging.info(
				f"📦 Gather: Collecting results from {len(state.results)} agents..."
			)

			# Combine all agent results
			combined = "\n\n".join(
				[
					f"=== {agent_name.upper()} ===\n{result}"
					for agent_name, result in state.results.items()
				]
			)

			# Detect audience type from query keywords
			query_lower = state.query.lower()
			if any(
				word in query_lower
				for word in [
					"technical",
					"specs",
					"professional",
					"developer",
					"engineer",
				]
			):
				audience = "technical"
			elif any(
				word in query_lower
				for word in ["consumer", "general", "buying", "purchase", "everyday"]
			):
				audience = "consumer"
			else:
				# Default: if both agents ran, assume comprehensive = consumer
				# If only tech agent ran, assume technical
				audience = "consumer" if len(state.results) > 1 else "technical"

			logging.info(f"📦 Gather: Detected audience type = '{audience}'")

			return {"gathered_data": combined, "audience_type": audience}

		# ================================================================
		# STEP 4: Synthesis Router - Decides which synthesizer to use
		# ================================================================

		synthesis_routing_prompt = """
You are a synthesis routing agent. Based on the audience type, decide which synthesizer should create the final report.

Available synthesizers:
- technical_synthesizer: Creates detailed technical reports for professionals, engineers, developers
- consumer_synthesizer: Creates accessible consumer reports for general buyers

Audience type: {audience_type}

Rules:
- If audience_type is "technical" → respond with: "technical_synthesizer"
- If audience_type is "consumer" → respond with: "consumer_synthesizer"

Respond with ONLY the synthesizer name, nothing else.
"""

		synthesis_router_model = ChatLLMProvider(
			provider="OpenRouter", model="google/gemini-2.5-flash"
		)

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def synthesis_router(state: GraphState) -> dict:
			"""Route to appropriate synthesizer based on audience."""
			logging.info(
				f"🧠 Synthesis Router: Analyzing audience type '{state.audience_type}'"
			)

			prompt = synthesis_routing_prompt.format(audience_type=state.audience_type)

			# Create input messages
			input_messages = [HumanMessage(content=prompt)]

			# Invoke the model directly
			result = await synthesis_router_model.ainvoke(input_messages)
			decision = result.content.strip().lower()

			# Normalize decision
			if "technical" in decision:
				decision = "technical_synthesizer"
			else:
				decision = "consumer_synthesizer"

			logging.info(f"✅ Synthesis Router decided: {decision}")

			return {
				"synthesis_decision": decision,
				"messages": input_messages
				+ [result],  # Includes all token/model metadata
			}

		# ================================================================
		# STEP 5: Synthesizers - Two different synthesis strategies
		# ================================================================

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def technical_synthesizer(state: GraphState) -> dict:
			"""Create a technical report for professional audiences."""
			logging.info(f"🔬 Technical Synthesizer: Creating professional report...")
			start = time.perf_counter()

			synthesizer = create_react_agent(
				model=ChatLLMProvider(
					provider="OpenRouter", model="google/gemini-2.5-flash"
				),
				tools=[],
			)

			prompt = f"""
You are a technical report writer for professional audiences (engineers, developers, IT professionals).

Create a comprehensive technical report based on the following research data:

{state.gathered_data}

Report requirements:
- Use technical terminology and industry jargon
- Focus on specifications, performance metrics, and technical details
- Include quantitative comparisons where possible
- Highlight technical advantages and limitations
- Format professionally with clear sections
- Keep report under 300 words but information-dense

Structure:
1. Technical Overview
2. Key Specifications
3. Performance Analysis
4. Technical Recommendations

Original query: {state.query}
"""

			result = await synthesizer.ainvoke(
				{
					"messages": [
						SystemMessage(
							content="You are a technical report writer specializing in product analysis for professionals."
						),
						HumanMessage(content=prompt),
					]
				}
			)

			report = result["messages"][-1].content.strip()

			elapsed = (time.perf_counter() - start) * 1000

			logging.info(f"🔬 Technical Synthesizer END - took_ms={elapsed:.1f}")

			return {
				"final_report": report,
				"messages": result["messages"],  # Include messages in the return dict
			}

		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def consumer_synthesizer(state: GraphState) -> dict:
			"""Create a consumer-friendly report for general audiences."""
			logging.info(f"🛒 Consumer Synthesizer: Creating consumer report...")
			start = time.perf_counter()

			synthesizer = create_react_agent(
				model=ChatLLMProvider(
					provider="OpenRouter", model="google/gemini-2.5-flash"
				),
				tools=[],
			)

			prompt = f"""
You are a consumer report writer for general audiences (everyday buyers, non-technical users).

Create an accessible, friendly report based on the following research data:

{state.gathered_data}

Report requirements:
- Use simple, clear language (avoid jargon)
- Focus on user benefits and real-world value
- Highlight what users love and common complaints
- Include practical buying advice
- Make it engaging and easy to read
- Keep report under 300 words but actionable

Structure:
1. What It Is (simple overview)
2. What Users Love
3. What to Watch Out For  
4. Should You Buy It? (recommendation)

Original query: {state.query}
"""

			result = await synthesizer.ainvoke(
				{
					"messages": [
						SystemMessage(
							content="You are a consumer report writer helping everyday people make informed buying decisions."
						),
						HumanMessage(content=prompt),
					]
				}
			)

			report = result["messages"][-1].content.strip()

			elapsed = (time.perf_counter() - start) * 1000

			logging.info(f"🛒 Consumer Synthesizer END - took_ms={elapsed:.1f}")

			return {
				"final_report": report,
				"messages": result["messages"],  # Include messages in the return dict
			}

		# ================================================================
		# Conditional Edge Functions
		# ================================================================

		def route_to_synthesizer(state: GraphState) -> str:
			"""Route to the appropriate synthesizer based on synthesis decision."""
			decision = state.synthesis_decision
			logging.info(f"🔀 Routing to: {decision}")
			return decision

		# ================================================================
		# STEP 6: Build the Graph with Introspection
		# ================================================================

		# Wrap nodes for introspection
		llm_router_intro = agents_manager.agent_introspector.introspect_node(
			llm_router, "llm_router"
		)
		tech_agent_intro = agents_manager.agent_introspector.introspect_node(
			technical_specs_agent, "technical_specs_agent"
		)
		reviews_agent_intro = agents_manager.agent_introspector.introspect_node(
			user_reviews_agent, "user_reviews_agent"
		)
		gather_intro = agents_manager.agent_introspector.introspect_node(
			gather_results, "gather_results"
		)
		synthesis_router_intro = agents_manager.agent_introspector.introspect_node(
			synthesis_router, "synthesis_router"
		)
		tech_synth_intro = agents_manager.agent_introspector.introspect_node(
			technical_synthesizer, "technical_synthesizer"
		)
		consumer_synth_intro = agents_manager.agent_introspector.introspect_node(
			consumer_synthesizer, "consumer_synthesizer"
		)

		# Register all nodes for report generation
		agents_manager.agent_introspector._all_nodes = [
			"llm_router",
			"technical_specs_agent",
			"user_reviews_agent",
			"gather_results",
			"synthesis_router",
			"technical_synthesizer",
			"consumer_synthesizer",
		]

		# Add nodes to graph
		graph.add_node("llm_router", llm_router_intro)
		graph.add_node("technical_specs_agent", tech_agent_intro)
		graph.add_node("user_reviews_agent", reviews_agent_intro)
		graph.add_node("gather_results", gather_intro)
		graph.add_node("synthesis_router", synthesis_router_intro)
		graph.add_node("technical_synthesizer", tech_synth_intro)
		graph.add_node("consumer_synthesizer", consumer_synth_intro)

		# Define edges
		graph.add_edge(START, "llm_router")

		# Conditional fan-out to worker agents
		graph.add_conditional_edges(
			"llm_router",
			supervisor_fan_out,
			path_map=["technical_specs_agent", "user_reviews_agent"],
		)

		# Worker agents converge to gather
		graph.add_edge("technical_specs_agent", "gather_results")
		graph.add_edge("user_reviews_agent", "gather_results")

		# Gather routes to synthesis router
		graph.add_edge("gather_results", "synthesis_router")

		# Conditional routing to synthesizers
		graph.add_conditional_edges(
			"synthesis_router",
			route_to_synthesizer,
			path_map={
				"technical_synthesizer": "technical_synthesizer",
				"consumer_synthesizer": "consumer_synthesizer",
			},
		)

		# Synthesizers route to end
		graph.add_edge("technical_synthesizer", END)
		graph.add_edge("consumer_synthesizer", END)

		# Compile the graph
		app = graph.compile()

		# ================================================================
		# STEP 7: Test the Workflow
		# ================================================================

		test_queries = [
			"I need a comprehensive technical analysis of the iPhone 15 Pro for professional developers. Taken into account user revews too as well as technical specs",
		]

		for query in test_queries:
			print("\n" + "=" * 100)
			logging.info(f"🚀 TESTING QUERY: '{query}'")
			print("=" * 100)

			wall_start = time.perf_counter()

			result = None
			try:
				state = GraphState(query=query)
				result = await app.ainvoke(state)
				wall_ms = (time.perf_counter() - wall_start) * 1000

			except Exception as e:
				logging.error(f"❌ Workflow execution failed: {str(e)}")
				raise
			finally:
				# Generate execution artifacts (report, graph)
				if result is not None:
					await agents_manager.generate_execution_artifacts(
						app, __file__, final_state=result
					)
				else:
					logger.warning("Result from execution are none")

			# Display results
			print(f"\n{'=' * 100}")
			print(f"📊 EXECUTION RESULTS")
			print(f"{'=' * 100}")
			print(f"Query: {result['query']}")
			print(f"Worker Agents Called: {result['routing_decision']}")
			print(f"Routing Rationale: {result['routing_rationale']}")
			print(f"Audience Type: {result['audience_type']}")
			print(f"Synthesizer Used: {result['synthesis_decision']}")
			print(f"\n{'─' * 100}")
			print(f"📄 FINAL REPORT:")
			print(f"{'─' * 100}")
			print(result.get("final_report", "N/A"))
			print(f"\n{'─' * 100}")
			logging.info(f"⏱️  TOTAL WALL TIME: {wall_ms:.1f}ms")
			print(f"{'=' * 100}\n")


if __name__ == "__main__":
	asyncio.run(main(), debug=True)
