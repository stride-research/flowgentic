"""
Simple async HTTP-like server for demonstrating SERVICE_TASK patterns.
This simulates a real production server with proper startup/shutdown lifecycle.
"""

import asyncio
import time
from datetime import datetime
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


class SimpleAsyncServer:
	"""
	A simple async server that mimics production HTTP server behavior.
	Maintains state, handles requests, and has proper lifecycle management.
	"""

	def __init__(self, host: str = "localhost", port: int = 8080):
		self.host = host
		self.port = port
		self.instance_id = f"server_{int(time.time() * 1000)}"
		self.started_at = datetime.now()
		self.request_count = 0
		self.is_running = False
		self._server_task: Optional[asyncio.Task] = None

	async def start(self):
		"""Start the server (mimics uvicorn.run() or similar)."""
		if self.is_running:
			logger.warning(f"Server {self.instance_id} is already running")
			return

		self.is_running = True
		print(
			f"\n❄️  COLD START: Server {self.instance_id} starting on {self.host}:{self.port}"
		)
		print(f"    Started at: {self.started_at.isoformat()}")

		# Simulate server startup time
		await asyncio.sleep(0.1)
		print(f"✅ Server {self.instance_id} is ready to accept connections\n")

	async def handle_request(self, endpoint: str, method: str = "GET") -> Dict:
		"""Handle an incoming request (mimics HTTP request handling)."""
		if not self.is_running:
			raise RuntimeError(f"Server {self.instance_id} is not running")

		self.request_count += 1
		uptime = (datetime.now() - self.started_at).total_seconds()

		# Simulate processing time
		await asyncio.sleep(0.01)

		return {
			"status": "success",
			"endpoint": endpoint,
			"method": method,
			"instance_id": self.instance_id,
			"request_number": self.request_count,
			"uptime_seconds": round(uptime, 2),
			"started_at": self.started_at.isoformat(),
			"timestamp": datetime.now().isoformat(),
		}

	async def shutdown(self):
		"""Gracefully shutdown the server."""
		if not self.is_running:
			return

		print(f"\n🔴 SHUTDOWN: Server {self.instance_id} shutting down...")
		print(f"    Total requests handled: {self.request_count}")
		print(
			f"    Total uptime: {(datetime.now() - self.started_at).total_seconds():.2f}s\n"
		)

		self.is_running = False

		# Simulate cleanup time
		await asyncio.sleep(0.05)

	async def health_check(self) -> Dict:
		"""Health check endpoint."""
		return {
			"status": "healthy" if self.is_running else "stopped",
			"instance_id": self.instance_id,
			"uptime": (datetime.now() - self.started_at).total_seconds(),
			"requests_handled": self.request_count,
		}


async def create_and_start_server(
	host: str = "localhost", port: int = 8080
) -> SimpleAsyncServer:
	"""Factory function to create and start a server instance."""
	server = SimpleAsyncServer(host, port)
	await server.start()
	return server
