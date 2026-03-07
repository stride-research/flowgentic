from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Callable, Dict, Optional

from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine
from flowgentic.backend_engines.queued_engine import QueuedEngine

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
			backend = await ConcurrentExecutionBackend(executor)
			flow = await WorkflowEngine.create(backend)
			yield AsyncFlowEngine(flow, observer=observer)
		finally:
			# 3. Shutdown the flow, then manually shut down the executor
			await flow.shutdown()
			executor.shutdown(wait=True)

	elif engine_id == "asyncflow_queued":
		# QueuedEngine wraps AsyncFlow with explicit open-loop queueing
		# This provides proper saturation semantics (fire-and-forget submission)
		ctx = multiprocessing.get_context("spawn")

		executor = ProcessPoolExecutor(max_workers=n_of_backend_slots, mp_context=ctx)

		try:
			backend = await ConcurrentExecutionBackend(executor)
			flow = await WorkflowEngine.create(backend)
			base_engine = AsyncFlowEngine(flow, observer=observer)

			# QueuedEngine manages its own worker pool matching backend slots
			queued = QueuedEngine(
				engine=base_engine,
				max_workers=n_of_backend_slots,
				observer=observer,
			)
			await queued.start()

			yield queued
		finally:
			await queued.stop()
			await flow.shutdown()
			executor.shutdown(wait=True)

	else:
		raise Exception(f"Didnt match any engine for engine_id: {engine_id}")
