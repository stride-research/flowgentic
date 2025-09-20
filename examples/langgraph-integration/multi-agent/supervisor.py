"""
1. Initial Request ProcessingUser Input: "I need weather and travel info for Paris to London trip"
    ↓
[Supervisor] → Analyzes request → Detects multiple agent keywords ("weather", "travel")
    ↓
Decision: Route to parallel execution (since multiple agents needed)

2. Parallel Execution Phase[Parallel Executor] → Launches AsyncFlow block
    ↓
┌─────────────────────────────────────────────────────────┐
│                 PARALLEL EXECUTION                       │
├─────────────────────────────────────────────────────────┤
│  [Weather Agent]           [Travel Agent]               │
│  ├─ get_weather_tool      ├─ find_flights_tool          │
│  ├─ analyze conditions    ├─ check_requirements         │
│  └─ return weather info   └─ return travel info         │
│                                                         │
│  Both run simultaneously via AsyncFlow                 │
└─────────────────────────────────────────────────────────┘
    ↓
Results collected when both complete

3. Synthesis Phase[Synthesizer] → Receives both agent results
    ↓
Combines weather + travel insights → Final comprehensive response
    ↓
[END] → Complete response delivered to user
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import HumanMessage
from radical.asyncflow import ConcurrentExecutionBackend

from flowgentic.langGraph.main import (
	EnhancedMultiAgentState,
	LangGraphIntegration,
	ReactAgentConfig,
	SupervisorConfig,
)
from flowgentic.llm_providers import ChatLLMProvider


async def example_react_supervisor_usage():
	"""Example showing how to use the enhanced integration."""
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangGraphIntegration(backend=backend) as integration:
		from langchain_openai import ChatOpenAI

		# Create LLM instances
		llm = ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash")

		# Create tools with AsyncFlow integration
		@integration.asyncflow_tool()
		async def weather_tool(city: str) -> str:
			await asyncio.sleep(1)
			return f"Weather in {city}: Sunny, 22°C"

		@integration.asyncflow_tool()
		async def travel_tool(origin: str, destination: str) -> str:
			await asyncio.sleep(2)
			return f"Travel from {origin} to {destination}: 3 hours by train"

		# Define React agent configurations
		agent_configs = [
			ReactAgentConfig(
				name="weather_agent",
				system_prompt="You are a weather specialist. Use tools to provide weather information.",
				tools=[weather_tool],
				llm=llm,
			),
			ReactAgentConfig(
				name="travel_agent",
				system_prompt="You are a travel specialist. Use tools to provide travel information.",
				tools=[travel_tool],
				llm=llm,
			),
		]

		# Supervisor configuration
		supervisor_config = SupervisorConfig(
			routing_llm=llm, synthesizer_llm=llm, enable_parallel_execution=True
		)

		# Create supervisor graph
		supervisor_graph = integration.create_supervisor_graph(
			agent_configs=agent_configs,
			supervisor_config=supervisor_config,
			state_class=EnhancedMultiAgentState,
		)

		# Test the system
		initial_state = EnhancedMultiAgentState(
			messages=[
				HumanMessage(
					content="I need weather and travel info for Paris to London trip"
				)
			]
		)

		result = await supervisor_graph.ainvoke(initial_state)
		print("Final result:", result["messages"][-1].content)


if __name__ == "__main__":
	asyncio.run(example_react_supervisor_usage())
