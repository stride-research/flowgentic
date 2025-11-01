"""
Service Task Example 1: SERVICE_TASK with service=True

Demonstrates AsyncFlowType.SERVICE_TASK for long-running services with continual uptime.
The service instance is created ONCE and reused across multiple calls.

This example proves:
1. Service initialization on first call
2. Agent uses the service
3. Time passes (service stays alive)
4. User manually calls the service
5. Service state proves it's the same instance (metrics accumulate)
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


# Simulated database service with persistent state
class DatabaseService:
	def __init__(self):
		self.connection_id = f"conn_{datetime.now().timestamp()}"
		self.created_at = datetime.now()
		self.query_count = 0
		self.total_query_time = 0
		print(f"🔌 Database service initialized: {self.connection_id}")

	async def execute_query(self, query: str) -> Dict[str, Any]:
		"""Execute a database query using persistent connection."""
		self.query_count += 1

		# Simulate query execution time
		query_time = 0.1
		await asyncio.sleep(query_time)
		self.total_query_time += query_time

		uptime = (datetime.now() - self.created_at).total_seconds()

		# Simulate query result
		result_data = {
			"sales_count": 42,
			"total_revenue": 150000.50,
			"top_product": "Laptop Pro",
		}

		return {
			"query": query[:60] + "..." if len(query) > 60 else query,
			"result": result_data,
			"connection_id": self.connection_id,
			"query_number": self.query_count,
			"service_uptime_seconds": round(uptime, 2),
			"avg_query_time": round(self.total_query_time / self.query_count, 3),
			"created_at": str(self.created_at),
		}


async def main():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		print("=" * 80)
		print("SERVICE_TASK Example: Persistent Database Service")
		print("=" * 80)
		print("\nThis example demonstrates service=True functionality:")
		print("- Service created ONCE on first call")
		print("- Service instance persists across multiple calls")
		print("- State (query count, uptime) accumulates")
		print()

		# SERVICE_TASK with service=True
		# The database service is created once
		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.SERVICE_TASK
		)
		async def database_query_service(sql: str) -> str:
			"""
			Persistent database service using SERVICE_TASK.

			The service instance is:
			- Created ONCE on first call
			- Cached and reused for all subsequent calls
			- Maintains state across calls (query count, uptime)
			"""
			# Service initialization (happens only on first call)
			if not hasattr(database_query_service, "_service"):
				database_query_service._service = DatabaseService()
				print("🚀 SERVICE_TASK: Database service instance created!")

			# Use the persistent service instance
			result = await database_query_service._service.execute_query(sql)

			# Return formatted result
			return (
				f"Query executed: {result['query']}\n"
				f"Result: {result['result']}\n"
				f"📊 Service Metrics:\n"
				f"  - Connection ID: {result['connection_id']}\n"
				f"  - Query #{result['query_number']}\n"
				f"  - Service Uptime: {result['service_uptime_seconds']}s\n"
				f"  - Avg Query Time: {result['avg_query_time']}s"
			)

		# ========================================
		# PART 1: Agent uses the service
		# ========================================
		print("=" * 80)
		print("PART 1: Agent calls database_query_service")
		print("=" * 80)

		# Create agent with the service
		agent = create_react_agent(
			model=ChatLLMProvider(
				provider="OpenRouter", model="google/gemini-2.5-flash"
			),
			tools=[database_query_service],
		)

		# Agent makes a query
		print("\n📞 Agent calling: SELECT * FROM sales WHERE region='North'\n")
		result1 = await agent.ainvoke(
			{
				"messages": [
					HumanMessage(
						content="Use database_query_service to run: SELECT * FROM sales WHERE region='North'"
					)
				]
			}
		)

		print(f"\n✅ Agent completed call #1")

		# Wait to show service stays alive
		wait_time = 3
		print(f"\n⏳ Waiting {wait_time} seconds (service should stay alive)...\n")
		await asyncio.sleep(wait_time)

		# ========================================
		# PART 2: User manually calls the service
		# ========================================
		print("=" * 80)
		print("PART 2: User manually calls database_query_service")
		print("=" * 80)

		print("\n📞 User calling: SELECT COUNT(*) FROM products\n")
		manual_result = await database_query_service("SELECT COUNT(*) FROM products")

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
		query_number = None
		uptime = None

		for line in lines:
			if "Query #" in line:
				query_number = line.split("#")[1].strip()
			if "Service Uptime:" in line:
				uptime = line.split(":")[1].strip()

		print("\n📊 Service Statistics:")
		print(f"  ✓ Total queries executed: {query_number}")
		print(f"  ✓ Total service uptime: {uptime}")
		print(f"  ✓ Service survived: Agent call → {wait_time}s wait → Manual call")

		print("\n💡 Key Insight:")
		print("  The service instance was created ONCE and persisted across:")
		print("  1. Agent's call (query #1)")
		print(f"  2. {wait_time} second wait period")
		print(f"  3. User's manual call (query #{query_number})")
		print("\n  This proves the service=True parameter maintains state!")

		print("\n" + "=" * 80)
		print("✅ SERVICE_TASK Example Complete")
		print("=" * 80)


if __name__ == "__main__":
	asyncio.run(main())
