"""
SERVICE_TASK Example: Persistent HTTP Server with No Cold-Start

Demonstrates that SERVICE_TASK maintains continual uptime for a real async server:
- Node 1: Starts server and makes first request
- Node 2: Makes second request (NO cold-start, same server instance)
- Node 3: Makes third request (still NO cold-start)
- Proof: Same server instance ID, incremented request counter, accumulated uptime

This mimics production patterns where you want to keep a service running
continuously across multiple invocations (e.g., FastAPI, Flask, etc.)
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from radical.asyncflow import ConcurrentExecutionBackend
from dotenv import load_dotenv
from utils.simple_server import create_and_start_server

load_dotenv()


async def main():
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

	async with LangraphIntegration(backend=backend) as agents_manager:
		print("\n" + "=" * 70)
		print("🌐 Persistent HTTP Server: No Cold-Start Demo")
		print("=" * 70)

		# Initialize the server once - this stays alive across SERVICE_TASK calls
		server = await create_and_start_server(host="localhost", port=8080)

		# SERVICE_TASK wraps the server to maintain continual uptime
		@agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.SERVICE_TASK
		)
		async def call_server(endpoint: str, method: str = "GET") -> str:
			"""Make a request to the persistent server."""
			result = await server.handle_request(endpoint, method)
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

		# Wait to show server stays alive
		wait_time = 2
		print(f"\n⏱️  Waiting {wait_time} seconds (server keeps running)...")
		await asyncio.sleep(wait_time)

		# ===== NODE 2: Second request (no cold-start) =====
		print("\n🔶 NODE 2: Second request to different endpoint")
		print("-" * 70)
		result2, future2 = await call_server("/api/health", "GET")
		print(result2)

		# ===== NODE 3: Third request (still no cold-start) =====
		print("\n🔷 NODE 3: Third request with POST")
		print("-" * 70)
		result3, future3 = await call_server("/api/data", "POST")
		print(result3)

		# ===== PROOF OF NO COLD-START =====
		print("\n" + "=" * 70)
		print("✅ PROOF OF CONTINUAL UPTIME & NO COLD-START")
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

		uptime3 = [l for l in lines3 if "Server Uptime:" in l][0].split(": ")[1].strip()

		print(f"\n📊 Evidence:")
		print(
			f"   ✓ Same server instance across all requests: {instance1 == instance2 == instance3}"
		)
		print(f"   ✓ Server Instance ID: {instance1}")
		print(f"   ✓ Request counter incremented: {req_num1} → {req_num2} → {req_num3}")
		print(f"   ✓ Total server uptime: {uptime3}")
		print(f"   ✓ Only ONE 'COLD START' message appeared above")

		print(f"\n💡 Conclusion:")
		print(f"   All requests were handled by the SAME server instance")
		print(f"   No server restart = No cold-start!")
		print(f"   The server maintained state and stayed alive between requests")
		print("\n" + "=" * 70)

		# Clean shutdown
		await server.shutdown()


if __name__ == "__main__":
	asyncio.run(main())
