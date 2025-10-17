"""
Example: SERVICE_TASK for persistent internal services

This example demonstrates how SERVICE_TASK caches service instances
for reuse across multiple calls, avoiding repeated initialization overhead.

Use SERVICE_TASK for:
- Database connection pools
- Redis/cache clients
- File watchers
- Any service that should persist between calls
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from radical.asyncflow.backends.execution.concurrent import ConcurrentExecutionBackend
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.execution_wrappers import AsyncFlowType


class DatabasePool:
	"""Simulates a database connection pool"""

	def __init__(self, connections: int = 10):
		self.connections = connections
		self.query_count = 0
		print(f"🔌 Initializing database pool with {connections} connections...")

	def query(self, sql: str):
		self.query_count += 1
		return f"Query result for: {sql} (total queries: {self.query_count})"


class RedisCache:
	"""Simulates a Redis cache client"""

	def __init__(self, host: str = "localhost"):
		self.host = host
		self.cache = {}
		print(f"🔌 Connecting to Redis at {host}...")

	def get(self, key: str):
		return self.cache.get(key)

	def set(self, key: str, value: str):
		self.cache[key] = value


async def main():
	print("=" * 60)
	print("SERVICE_TASK Example: Persistent Service Instances")
	print("=" * 60)

	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as integration:
		# Define persistent database pool service
		@integration.execution_wrappers.asyncflow(flow_type=AsyncFlowType.SERVICE_TASK)
		async def database_pool():
			"""Creates a persistent database connection pool"""
			return DatabasePool(connections=10)

		# Define persistent Redis cache service
		@integration.execution_wrappers.asyncflow(flow_type=AsyncFlowType.SERVICE_TASK)
		async def redis_cache():
			"""Creates a persistent Redis cache client"""
			return RedisCache(host="localhost")

		print("\n📍 First calls (initializes services):")
		print("-" * 60)

		# First calls - initialize services
		db = await database_pool()
		cache = await redis_cache()

		# Use the services
		print(f"\n📊 Using database pool:")
		print(f"  {db.query('SELECT * FROM users')}")
		print(f"  {db.query('SELECT * FROM products')}")

		print(f"\n💾 Using Redis cache:")
		cache.set("user:1", "Diana")
		print(f"  Set user:1 = Diana")
		print(f"  Get user:1 = {cache.get('user:1')}")

		print("\n📍 Second calls (reuses cached instances):")
		print("-" * 60)

		# Second calls - get cached instances
		db2 = await database_pool()
		cache2 = await redis_cache()

		# Verify they're the same instances
		print(f"\n✅ Verification:")
		print(f"  Database pool same instance? {db is db2}")
		print(f"  Redis cache same instance? {cache is cache2}")
		print(f"  Database query count persisted? {db2.query_count == 2}")
		print(f"  Redis cache persisted? {cache2.get('user:1') == 'Diana'}")

		# More queries to show persistence
		print(f"\n📊 More database queries:")
		print(f"  {db2.query('SELECT * FROM orders')}")
		print(f"  Total queries on persisted pool: {db2.query_count}")

		print("\n" + "=" * 60)
		print("✨ SERVICE_TASK successfully cached and reused instances!")
		print("=" * 60)


if __name__ == "__main__":
	asyncio.run(main())
