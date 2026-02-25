from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Optional

from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine
import parsl
from flowgentic.backend_engines.parsl import ParslEngine
from parsl.config import Config
from parsl.executors import ThreadPoolExecutor as ParslThreadPoolExecutor


@asynccontextmanager
async def resolve_engine(
	engine_id: str,
	n_of_backend_slots: int,
	observer: Optional[Callable[[Dict[str, Any]], None]] = None,
):
	if engine_id == "asyncflow":
		executor = ThreadPoolExecutor(max_workers=n_of_backend_slots)

		try:
			backend = await ConcurrentExecutionBackend(executor)
			flow = await WorkflowEngine.create(backend)
			yield AsyncFlowEngine(flow, observer=observer)
		finally:
			# 3. Shutdown the flow, then manually shut down the executor
			await flow.shutdown()
			executor.shutdown(wait=True)
	elif engine_id == "parsl":
		parsl_config = Config(
			executors=[ParslThreadPoolExecutor(max_threads=n_of_backend_slots, label="local_threads")]
		)
		try:
			yield ParslEngine(config=parsl_config, observer=observer)
		finally:
			parsl.dfk().cleanup()
	else:
		raise Exception(f"Didnt match any engine for engine_id: {engine_id}")
