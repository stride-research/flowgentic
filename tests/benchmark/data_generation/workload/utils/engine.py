from autogen.code_utils import ThreadPoolExecutor
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine


async def resolve_engine(engine_id: str, backend_workers: int):
	if engine_id == "asyncflow":
		backend = await ConcurrentExecutionBackend(
			ThreadPoolExecutor(max_workers=backend_workers)
		)
		flow = await WorkflowEngine.create(backend)
		return AsyncFlowEngine(flow)
