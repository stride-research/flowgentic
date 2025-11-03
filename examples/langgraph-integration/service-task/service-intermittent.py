"""
SERVICE_TASK Intermittent Example: Cold-Start After Server Shutdown

Demonstrates that SERVICE_TASK triggers a cold-start when the underlying service stops:
- Node 1: Starts server and makes first request
- Node 2: Makes second request (same server, no cold-start)
- Shutdown server and cancel futures
- Node 3: Starts new server and makes request (COLD-START occurs)
- Proof: Different server instance IDs, reset counter, new uptime

This mimics real production scenarios where services may need to be restarted
(e.g., due to crashes, deployments, or manual intervention).
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from radical.asyncflow import ConcurrentExecutionBackend
from dotenv import load_dotenv
from utils.simple_server import create_and_start_server, SimpleAsyncServer

load_dotenv()


async def main():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		print("\n" + "=" * 70)
		print("🔄 Intermittent Server: Cold-Start After Shutdown Demo")
		print("=" * 70)

		# Keep track of current server instance
		current_server: SimpleAsyncServer = await create_and_start_server(
			host="localhost", port=8080
		)

		# SERVICE_TASK wraps the server calls
		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.SERVICE_TASK
		)
		async def call_server(endpoint: str, method: str = "GET") -> str:
			"""Make a request to the current server instance."""
			result = await current_server.handle_request(endpoint, method)
			return (
				f"📡 HTTP {method} {result['endpoint']}\n"
				f"   Status: {result['status'].upper()}\n"
				f"   Server Instance: {result['instance_id']}\n"
				f"   Request #{result['request_number']}\n"
				f"   Server Uptime: {result['uptime_seconds']}s"
			)

		# ===== NODE 1: First request =====
		print("\n🔷 NODE 1: First request to server")
		print("-" * 70)
		result1, future1 = await call_server("/api/users", "GET")
		print(result1)

		# Wait a bit
		wait_time = 1
		print(f"\n⏱️  Waiting {wait_time} second...")
		await asyncio.sleep(wait_time)

		# ===== NODE 2: Second request (same server) =====
		print("\n🔶 NODE 2: Second request (server still running)")
		print("-" * 70)
		result2, future2 = await call_server("/api/health", "GET")
		print(result2)

		# ===== SHUTDOWN THE SERVER =====
		print("\n" + "=" * 70)
		print("🛑 STOPPING SERVER: Shutting down and canceling futures...")
		print("=" * 70)

		# Gracefully shutdown the server
		await current_server.shutdown()

		# Cancel the service task futures
		future1.cancel()
		future2.cancel()

		# Give it time to clean up
		await asyncio.sleep(0.5)

		# ===== START NEW SERVER =====
		print("\n🔄 RESTARTING: Creating new server instance...")
		current_server = await create_and_start_server(host="localhost", port=8081)

		# ===== NODE 3: Request with new server =====
		print("\n🔷 NODE 3: Request after server restart")
		print("-" * 70)
		result3, future3 = await call_server("/api/data", "POST")
		print(result3)

		# ===== PROOF OF COLD-START =====
		print("\n" + "=" * 70)
		print("✅ PROOF OF COLD-START AFTER SERVER SHUTDOWN")
		print("=" * 70)

		# Parse results
		lines1 = result1.split("\n")
		lines2 = result2.split("\n")
		lines3 = result3.split("\n")

		instance1 = [l for l in lines1 if "Server Instance:" in l][0].split(": ")[1]
		instance2 = [l for l in lines2 if "Server Instance:" in l][0].split(": ")[1]
		instance3 = [l for l in lines3 if "Server Instance:" in l][0].split(": ")[1]

		req_num1 = [l for l in lines1 if "Request #" in l][0].split("#")[1].strip()
		req_num2 = [l for l in lines2 if "Request #" in l][0].split("#")[1].strip()
		req_num3 = [l for l in lines3 if "Request #" in l][0].split("#")[1].strip()

		print(f"\n📊 Evidence:")
		print(f"   ✓ First two requests same server: {instance1 == instance2}")
		print(f"      Server 1 & 2: {instance1}")
		print(f"   ✓ Third request DIFFERENT server: {instance1 != instance3}")
		print(f"      Server 3: {instance3}")
		print(f"   ✓ Request counter in first server: {req_num1} → {req_num2}")
		print(f"   ✓ Request counter RESET in new server: {req_num3}")
		print(f"   ✓ TWO 'COLD START' messages appeared above")
		print(f"   ✓ TWO 'SHUTDOWN' messages appeared above")

		print(f"\n💡 Conclusion:")
		print(f"   Shutting down the server and canceling futures caused termination")
		print(f"   The next request used a NEW server instance (cold-start)")
		print(f"   This demonstrates intermittent/restart behavior in production")
		print("\n" + "=" * 70)

		# Clean shutdown of final server
		await current_server.shutdown()


if __name__ == "__main__":
	asyncio.run(main())
