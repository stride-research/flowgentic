from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Optional

from autogen.code_utils import ThreadPoolExecutor
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine


@asynccontextmanager
async def resolve_engine(
	engine_id: str,
	n_of_backend_slots: int,
	observer: Optional[Callable[[Dict[str, Any]], None]] = None,
):
	"""
	Create and return the appropriate engine based on engine_id.

	Args:
		engine_id: Identifier for the engine type
		n_of_backend_slots: Number of worker slots for the backend
		observer: Optional callback for profiling events
	"""
	if engine_id == "asyncflow":
		try:
			backend = await ConcurrentExecutionBackend(
				ProcessPoolExecutor(max_workers=n_of_backend_slots)
			)
			flow = await WorkflowEngine.create(backend)
			yield AsyncFlowEngine(flow, observer=observer)
		finally:
			await flow.shutdown()
	else:
		raise Exception(f"Didnt match any engine for engine_id: {engine_id}")
