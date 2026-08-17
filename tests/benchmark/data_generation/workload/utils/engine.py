from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Optional

from radical.asyncflow import LocalExecutionBackend, WorkflowEngine

from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine

import multiprocessing


@asynccontextmanager
async def resolve_engine(
	engine_id: str,
	n_of_backend_slots: int,
	observer: Optional[Callable[[Dict[str, Any]], None]] = None,
):
	if engine_id == "asyncflow":
		ctx = multiprocessing.get_context("spawn")

		executor = ProcessPoolExecutor(max_workers=n_of_backend_slots, mp_context=ctx)

		try:
			backend = await LocalExecutionBackend(executor)
			flow = await WorkflowEngine.create(backend)
			yield AsyncFlowEngine(flow, observer=observer)
		finally:
			# 3. Shutdown the flow, then manually shut down the executor
			await flow.shutdown()
			executor.shutdown(wait=True)
	else:
		raise Exception(f"Didnt match any engine for engine_id: {engine_id}")
