import asyncio
import logging
import time
from dotenv import load_dotenv

from radical.asyncflow import LocalExecutionBackend, WorkflowEngine
from concurrent.futures import ThreadPoolExecutor

from academy.agent import Agent, action
from academy.exchange.local import LocalExchangeFactory
from academy.manager import Manager

from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine
from flowgentic.agent_orchestration_frameworks.academy import AcademyOrchestrator

load_dotenv()

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class WeatherAgent(Agent):
	"""Academy agent that fetches weather data via HPC-backed tools."""

	def __init__(self, fetch_temperature_fn, fetch_humidity_fn) -> None:
		self._fetch_temperature = fetch_temperature_fn
		self._fetch_humidity = fetch_humidity_fn

	@action
	async def get_weather(self, location: str = "SFO") -> dict:
		"""Fetch temperature and humidity for a location."""
		temperature = await self._fetch_temperature(location=location)
		humidity = await self._fetch_humidity(location=location)
		return {**temperature, **humidity}


async def start_app():
	# --- SETUP HPC BACKEND ---
	backend = await LocalExecutionBackend(ThreadPoolExecutor(max_workers=2))
	flow = await WorkflowEngine.create(backend)

	# --- INITIALIZE FLOWGENTIC ---
	engine = AsyncFlowEngine(flow)
	orchestrator = AcademyOrchestrator(engine)

	# --- DEFINE HPC TOOLS ---
	@orchestrator.hpc_task
	async def fetch_temperature(location: str = "SFO") -> dict:
		"""Fetches temperature of a given city."""
		logger.info(f"Executing temperature tool for {location}")
		await asyncio.sleep(2)
		return {"temperature": 70, "location": location}

	@orchestrator.hpc_task
	async def fetch_humidity(location: str = "SFO") -> dict:
		"""Fetches humidity of a given city."""
		logger.info(f"Executing humidity tool for {location}")
		await asyncio.sleep(2)
		return {"humidity": 50, "location": location}

	# --- LAUNCH AGENT AND EXECUTE ---
	t_start = time.perf_counter()

	manager = await Manager.from_exchange_factory(
		factory=LocalExchangeFactory(),
		executors=None,
	)

	async with manager:
		handle = await manager.launch(
			WeatherAgent,
			args=(fetch_temperature, fetch_humidity),
		)
		result = await handle.get_weather(location="SFO")
		logger.info(f"Weather result: {result}")

	t_end = time.perf_counter()
	logger.info(f"Workflow finished in {t_end - t_start:.4f} seconds")

	await flow.shutdown()


if __name__ == "__main__":
	asyncio.run(start_app())
