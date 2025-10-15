"""
Example: TOOL_AS_SERVICE with Real LLM

This example demonstrates the KEY DIFFERENCE from SERVICE_TASK:
- TOOL_AS_SERVICE creates tools that LLMs can call
- The LLM decides WHEN to use the tool based on user questions
- Connection stays persistent across multiple LLM tool calls

Requires: OPENROUTER_API_KEY in .env file
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from radical.asyncflow.backends.execution.concurrent import ConcurrentExecutionBackend
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.utils.llm_providers import ChatLLMProvider
from langgraph.prebuilt import create_react_agent
from dotenv import load_dotenv

load_dotenv()


class WeatherAPIService:
	"""Simulates a weather API with persistent connection"""

	def __init__(self):
		print("\n🌐 [SERVICE] Establishing connection to Weather API...")
		print("🔐 [SERVICE] Authenticating with API token...")
		self.auth_token = "token_12345"
		self.request_count = 0
		self.cache = {}
		print("✅ [SERVICE] Weather API connected and ready!\n")

	async def handle_request(self, city: str):
		"""Handle weather request for a city"""
		self.request_count += 1

		# Check cache
		if city in self.cache:
			print(
				f"  ⚡ [SERVICE] Cache hit for {city}! (Request #{self.request_count})"
			)
			return self.cache[city]

		# Simulate API call
		print(
			f"  🌍 [SERVICE] Fetching weather for {city}... (Request #{self.request_count})"
		)

		# Fake weather data
		weather_data = {
			"New York": {"temperature": 72, "condition": "Sunny"},
			"Los Angeles": {"temperature": 68, "condition": "Cloudy"},
			"Chicago": {"temperature": 55, "condition": "Rainy"},
			"Miami": {"temperature": 85, "condition": "Hot and Humid"},
		}

		weather = weather_data.get(city, {"temperature": 70, "condition": "Unknown"})
		result = f"The weather in {city} is {weather['condition'].lower()} with a temperature of {weather['temperature']}°F"

		# Cache result
		self.cache[city] = result
		return result


async def main():
	print("=" * 80)
	print("TOOL_AS_SERVICE with Real LLM: Demonstrating LLM Tool Usage")
	print("=" * 80)

	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as integration:
		# Define the TOOL_AS_SERVICE (LLM-callable with caching)
		@integration.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_SERVICE
		)
		async def get_weather(city: str):
			"""Get the current weather for a given city. Use this when users ask about weather."""
			return WeatherAPIService()

		print("\n📋 Tool created: 'get_weather'")
		print(f"   Tool type: {type(get_weather)}")
		print(f"   Tool name: {get_weather.name}")
		print(f"   Tool description: {get_weather.description}")

		# Create LLM and ReAct agent
		print("\n🤖 Creating ReAct agent with weather tool...")
		llm = ChatLLMProvider(
			provider="OpenRouter", model="google/gemini-2.0-flash-exp:free"
		)
		agent = create_react_agent(llm, tools=[get_weather])
		print("✅ Agent created! It will automatically call tools when needed.")

		print("\n" + "=" * 80)
		print("User Conversation with LLM")
		print("=" * 80)

		# Question 1: Weather (LLM will call tool)
		print("\n👤 User: What's the weather like in New York?")
		response1 = await agent.ainvoke(
			{"messages": [("user", "What's the weather like in New York?")]}
		)
		print(f"🤖 Assistant: {response1['messages'][-1].content}\n")

		# Question 2: Weather (LLM will call tool again, reuses connection!)
		print("-" * 80)
		print("\n👤 User: How about Los Angeles?")
		response2 = await agent.ainvoke(
			{"messages": [("user", "How about Los Angeles?")]}
		)
		print(f"🤖 Assistant: {response2['messages'][-1].content}\n")

		# Question 3: Non-weather (LLM won't call tool)
		print("-" * 80)
		print("\n👤 User: What's 2 + 2?")
		response3 = await agent.ainvoke({"messages": [("user", "What's 2 + 2?")]})
		print(f"🤖 Assistant: {response3['messages'][-1].content}\n")

		# Question 4: Repeat weather (cached service + cached result!)
		print("-" * 80)
		print("\n👤 User: Tell me about New York's weather again")
		response4 = await agent.ainvoke(
			{"messages": [("user", "Tell me about New York's weather again")]}
		)
		print(f"🤖 Assistant: {response4['messages'][-1].content}\n")

		print("=" * 80)
		print("✨ What Just Happened:")
		print("=" * 80)
		print("1. ✅ Service initialized ONCE (first weather question)")
		print("2. ✅ LLM called tool 3 times (Questions 1, 2, 4)")
		print("3. ✅ LLM did NOT call tool for question 3 (no weather needed)")
		print("4. ✅ Connection stayed open (no re-authentication)")
		print("5. ✅ Results cached (New York weather hit cache on 2nd request)")
		print("=" * 80)


if __name__ == "__main__":
	asyncio.run(main())
