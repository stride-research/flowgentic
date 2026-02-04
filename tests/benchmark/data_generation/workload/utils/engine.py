from concurrent.futures import ProcessPoolExecutor
from autogen.code_utils import ThreadPoolExecutor
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine


async def resolve_engine(engine_id: str, n_of_backend_slots: int):
	"""Here you could implement an if-else for the different engine id and their corresponding objects"""
	if engine_id == "asyncflow":
		backend = await ConcurrentExecutionBackend(
			ProcessPoolExecutor(max_workers=n_of_backend_slots)
		)
		flow = await WorkflowEngine.create(backend)
		return AsyncFlowEngine(flow)
	raise Exception(f"Didnt match any engine for engine_id: {engine_id}")
