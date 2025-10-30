"""
Service Task Example 2: AGENT_TOOL_AS_SERVICE with service=True

Demonstrates AsyncFlowType.AGENT_TOOL_AS_SERVICE for persistent tool services.
The service instance is created ONCE and wrapped as a LangChain tool for agents.

This example proves:
1. Service initialization on first call
2. Agent uses the tool (which wraps the service)
3. Time passes (service stays alive)
4. User manually calls the tool
5. Service state proves it's the same instance (request count and uptime accumulate)
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any
from datetime import datetime
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.utils.llm_providers import ChatLLMProvider
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage
from radical.asyncflow import ConcurrentExecutionBackend
from dotenv import load_dotenv

load_dotenv()


# Simulated API client with persistent session
class WeatherAPIClient:
	def __init__(self):
		self.session_id = f"session_{datetime.now().timestamp()}"
		self.created_at = datetime.now()
		self.request_count = 0
		print(f"🌐 Weather API client session created: {self.session_id}")

	async def fetch_weather(self, city: str) -> Dict[str, Any]:
		"""Fetch weather using persistent HTTP session."""
		self.request_count += 1

		# Simulate API call
		await asyncio.sleep(0.15)

		# Simulate fresh weather data each time
		import random

		weather_data = {
			"temperature": random.randint(60, 85),
			"conditions": random.choice(["Sunny", "Cloudy", "Rainy"]),
			"humidity": random.randint(40, 80),
		}

		uptime = (datetime.now() - self.created_at).total_seconds()

		return {
			"city": city,
			"weather": weather_data,
			"session_id": self.session_id,
			"request_number": self.request_count,
			"session_uptime": round(uptime, 2),
		}


async def main():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		print("=" * 80)
		print("AGENT_TOOL_AS_SERVICE Example: Persistent Weather API Tool")
		print("=" * 80)
		print("\nThis example demonstrates service=True with tool wrapper:")
		print("- Service created ONCE on first call")
		print("- Wrapped as @tool for agent use")
		print("- Service instance persists across multiple calls")
		print("- State (request count, uptime) accumulates")
		print()

		# Creates a LangChain tool with persistent service instance
		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_SERVICE,
			tool_description="Weather API tool with persistent session. Maintains metrics across calls.",
		)
		async def weather_api_tool(city: str) -> str:
			"""
			Persistent weather API tool using AGENT_TOOL_AS_SERVICE.

			The service instance is:
			- Created ONCE on first call (lazy initialization)
			- Cached and reused for all subsequent calls
			- Wrapped as @tool for agent use
			- Maintains state across calls (request count, uptime)
			"""
			# Service initialization (happens only on first call)
			if not hasattr(weather_api_tool, "_client"):
				weather_api_tool._client = WeatherAPIClient()
				print("🚀 AGENT_TOOL_AS_SERVICE: Weather API client session created!")

			# Use the persistent service instance
			result = await weather_api_tool._client.fetch_weather(city)

			# Return formatted result
			return (
				f"Weather for {result['city']}:\n"
				f"  Temperature: {result['weather']['temperature']}°F\n"
				f"  Conditions: {result['weather']['conditions']}\n"
				f"  Humidity: {result['weather']['humidity']}%\n"
				f"\n📊 Service Metrics:\n"
				f"  - Session ID: {result['session_id']}\n"
				f"  - Request #{result['request_number']}\n"
				f"  - Session Uptime: {result['session_uptime']}s"
			)

		# ========================================
		# PART 1: Agent uses the tool
		# ========================================
		print("=" * 80)
		print("PART 1: Agent calls weather_api_tool")
		print("=" * 80)

		# Create agent with the service tool
		agent = create_react_agent(
			model=ChatLLMProvider(
				provider="OpenRouter", model="google/gemini-2.5-flash"
			),
			tools=[weather_api_tool],
		)

		# Agent makes first request
		print("\n📞 Agent calling: Get weather for San Francisco\n")
		result1 = await agent.ainvoke(
			{
				"messages": [
					HumanMessage(
						content="Use weather_api_tool to get the weather for San Francisco"
					)
				]
			}
		)

		print(f"\n✅ Agent completed call #1")

		# Agent makes second request (same session!)
		print("\n📞 Agent calling: Get weather for Seattle\n")
		result2 = await agent.ainvoke(
			{
				"messages": [
					HumanMessage(
						content="Use weather_api_tool to get the weather for Seattle"
					)
				]
			}
		)

		print(f"\n✅ Agent completed call #2")

		# Wait to show service stays alive
		wait_time = 3
		print(f"\n⏳ Waiting {wait_time} seconds (service should stay alive)...\n")
		await asyncio.sleep(wait_time)

		# ========================================
		# PART 2: User manually calls the tool
		# ========================================
		print("=" * 80)
		print("PART 2: User manually calls weather_api_tool")
		print("=" * 80)

		# Note: Since it's wrapped as @tool, we need to use .ainvoke() with dict input
		print("\n📞 User calling: Get weather for New York\n")
		manual_result = await weather_api_tool.ainvoke({"city": "New York"})

		print(f"\n✅ Manual call completed")
		print(f"\n{manual_result}")

		# ========================================
		# PART 3: Proof of continual uptime
		# ========================================
		print("\n" + "=" * 80)
		print("🎯 PROOF OF CONTINUAL UPTIME")
		print("=" * 80)

		# Extract metrics from the manual result
		lines = manual_result.split("\n")
		request_number = None
		uptime = None

		for line in lines:
			if "Request #" in line:
				request_number = line.split("#")[1].strip()
			if "Session Uptime:" in line:
				uptime = line.split(":")[1].strip()

		print("\n📊 Service Statistics:")
		print(f"  ✓ Total requests executed: {request_number}")
		print(f"  ✓ Total session uptime: {uptime}")
		print(
			f"  ✓ Service survived: Agent call #1 → Agent call #2 → {wait_time}s wait → Manual call"
		)

		print("\n💡 Key Insights:")
		print("  1. Same HTTP session used for all 3 requests")
		print(f"  2. Session uptime shows {uptime} of continuous operation")
		print("  3. Request count accumulated across agent AND manual calls")
		print("  4. Session ID stayed the same (proves same service instance)")

		print("\n" + "=" * 80)
		print("✅ AGENT_TOOL_AS_SERVICE Example Complete")
		print("=" * 80)


if __name__ == "__main__":
	asyncio.run(main())
