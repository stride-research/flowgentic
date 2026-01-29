import os
import asyncio
import logging
import time
from dotenv import load_dotenv

from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine
from concurrent.futures import ThreadPoolExecutor

import autogen
from autogen import AssistantAgent, UserProxyAgent

from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine
from flowgentic.agent_orchestration_frameworks.autogen import AutoGenOrchestrator
from flowgentic.core.models.implementations.dummy.autogen import (
	create_assistant_with_dummy_model,
)
from flowgentic.core.models.implementations.dummy.autogen import DummyAutoGenClient


load_dotenv()

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def start_app():
	# --- SETUP HPC BACKEND ---
	backend = await ConcurrentExecutionBackend(ThreadPoolExecutor(max_workers=2))
	flow = await WorkflowEngine.create(backend)

	# --- INITIALIZE FLOWGENTIC ---
	engine = AsyncFlowEngine(flow)
	orchestrator = AutoGenOrchestrator(engine)

	# --- DEFINE HPC TOOLS ---
	@orchestrator.hpc_tool
	async def fetch_temperature(location: str = "SFO") -> dict:
		"""Fetches temperature of a given city."""
		logger.info(f"Executing temperature tool for {location}")
		await asyncio.sleep(2)
		return {"temperature": 70, "location": location}

	@orchestrator.hpc_tool
	async def fetch_humidity(location: str = "SFO") -> dict:
		"""Fetches humidity of a given city."""
		logger.info(f"Executing humidity tool for {location}")
		await asyncio.sleep(2)
		return {"humidity": 50, "location": location}

	# --- DEFINE AGENTS ---
	assistant = create_assistant_with_dummy_model(  # Only for this dummy-model scenario u need to use this funct
		name="hpc_assistant",
		system_message="You are a helpful assistant. You can check weather data using available tools.",
	)

	# The User Proxy: Executes the tool calls
	user_proxy = UserProxyAgent(
		name="hpc_executor",
		human_input_mode="NEVER",
		max_consecutive_auto_reply=10,
		is_termination_msg=lambda x: x.get("content", "")
		.rstrip()
		.endswith("TERMINATE"),
		code_execution_config=False,
	)

	# --- REGISTER TOOLS ---
	autogen.register_function(
		fetch_temperature,
		caller=assistant,
		executor=user_proxy,
		description="Fetches temperature of a given city",
	)

	autogen.register_function(
		fetch_humidity,
		caller=assistant,
		executor=user_proxy,
		description="Fetches humidity of a given city",
	)

	# === ONLY FOR DUMMY MODEL ===
	if hasattr(assistant, "client") and assistant.client:
		assistant.client.register_model_client(model_client_cls=DummyAutoGenClient)

	# --- EXECUTE WORKFLOW ---
	t_start = time.perf_counter()

	input_message = "Fetch temperature and humidity in SFO. When done, reply TERMINATE."

	# a_initiate_chat is the async entry point for AutoGen
	await user_proxy.a_initiate_chat(assistant, message=input_message)

	t_end = time.perf_counter()
	logger.info(f"Workflow finished in {t_end - t_start:.4f} seconds")


if __name__ == "__main__":
	asyncio.run(start_app())
