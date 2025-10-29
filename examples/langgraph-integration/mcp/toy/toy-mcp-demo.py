"""
Toy MCP Example: Anthropic Demo Server

Demonstrates AGENT_TOOL_AS_MCP with Anthropic's demo MCP server.
This server provides simple tools like echo, add, multiply for testing.

Key Features:
- Automatic tool discovery from MCP server
- MCP agent executes through AsyncFlow backend
- Agent intelligently selects which tool to use
- Simple example for understanding MCP integration
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


async def main():
	print("=" * 80)
	print("MCP Toy Example: Anthropic Demo Server")
	print("=" * 80)
	print("\nThis example demonstrates:")
	print("✓ Connecting to Anthropic's demo MCP server")
	print("✓ Automatic tool discovery (echo, add, multiply, etc.)")
	print("✓ Agent intelligently selecting tools")
	print("✓ Execution through AsyncFlow backend\n")

	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor(max_workers=4))

	async with LangraphIntegration(backend=backend) as integration:
		# Create MCP specialist using official library + AsyncFlow
		@integration.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_MCP,
			tool_description="Toy MCP specialist with math and echo tools",
			mcp_servers={
				"demo": {
					"command": "npx",
					"args": ["-y", "@modelcontextprotocol/server-everything"],
					"transport": "stdio",
				}
			},
		)
		async def toy_mcp_specialist(question: str):
			"""
			MCP specialist connected to Anthropic's demo server.
			Has access to: echo, add, multiply, longRunningOperation, etc.
			"""
			pass

		print("🔌 MCP specialist created")
		print(f"   Tool name: {toy_mcp_specialist.name}")
		print(f"   Description: {toy_mcp_specialist.description}")

		# Test 1: Direct tool usage
		print("\n" + "=" * 80)
		print("Test 1: Direct MCP Tool Usage")
		print("=" * 80)

		questions = [
			"Echo back: Hello from FlowGentic!",
			"What's 15 + 27?",
			"Multiply 8 by 12",
		]

		for q in questions:
			print(f"\n📝 Question: {q}")
			result = await toy_mcp_specialist.ainvoke({"question": q})
			print(f"🤖 Response: {result}")

		# Test 2: Supervisor with MCP specialist
		print("\n" + "=" * 80)
		print("Test 2: Supervisor Agent with MCP Specialist")
		print("=" * 80)

		# Create other tools
		from langchain_core.tools import tool

		@tool
		def get_time():
			"""Get the current time"""
			from datetime import datetime

			return datetime.now().strftime("%H:%M:%S")

		# Supervisor with multiple tools
		llm = ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash")
		supervisor = create_react_agent(llm, tools=[toy_mcp_specialist, get_time])

		print("\n👥 Supervisor created with 2 tools:")
		print("   - toy_mcp_specialist (MCP agent)")
		print("   - get_time (simple function)")

		# Supervisor decides which tool to use
		test_queries = [
			"What's 100 + 234?",
			"What time is it?",
			"Echo 'Testing MCP' and tell me the current time",
		]

		for query in test_queries:
			print(f"\n📝 User: {query}")
			result = await supervisor.ainvoke({"messages": [("user", query)]})
			print(f"🤖 Supervisor: {result['messages'][-1].content}")

		# Test 3: Parallel execution through AsyncFlow
		print("\n" + "=" * 80)
		print("Test 3: Parallel Execution (AsyncFlow Backend)")
		print("=" * 80)

		parallel_questions = [
			"What's 5 + 3?",
			"What's 10 * 2?",
			"Echo 'parallel test'",
			"What's 50 + 50?",
		]

		print(f"\n🚀 Submitting {len(parallel_questions)} questions in parallel...")

		# All execute through AsyncFlow backend
		futures = [
			toy_mcp_specialist.ainvoke({"question": q}) for q in parallel_questions
		]
		results = await asyncio.gather(*futures)

		print("✅ All completed! Results:")
		for q, r in zip(parallel_questions, results):
			print(f"   Q: {q}")
			print(f"   A: {r}\n")


if __name__ == "__main__":
	asyncio.run(main())
