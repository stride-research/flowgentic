from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Optional

from autogen.code_utils import ThreadPoolExecutor
from radical.asyncflow import WorkflowEngine, DragonExecutionBackendV2

from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine

import multiprocessing as mp

@asynccontextmanager
async def resolve_engine(
	engine_id: str,
	n_of_backend_slots: int,
	observer: Optional[Callable[[Dict[str, Any]], None]] = None,
):
	if engine_id == "asyncflow":
		# Set Dragon as multiprocessing backend
		mp.set_start_method("dragon")

		try:
			backend = await DragonExecutionBackendV2()
			flow = await WorkflowEngine.create(backend)
			yield AsyncFlowEngine(flow, observer=observer)
		finally:
			# 3. Shutdown the flow, then manually shut down the executor
			await flow.shutdown()
	else:
		raise Exception(f"Didnt match any engine for engine_id: {engine_id}")
