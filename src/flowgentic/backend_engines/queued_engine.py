"""
QueuedEngine - Open-loop queueing wrapper for backend engines.

This wrapper provides proper queueing semantics for saturation experiments:
- Explicit queue buffer between submission and execution
- Fire-and-forget submission (non-blocking)
- Worker pool that pulls from queue
- Event emission for queue timing analysis

Key events emitted:
- t_submit: Task submitted to queue (returns immediately)
- t_dispatched: Worker picks up task from queue (queue wait ends)
- t_complete: Task execution finished

This enables measuring:
- Queue delay: t_dispatched - t_submit
- Execution time: t_complete - t_dispatched
- End-to-end latency: t_complete - t_submit
"""

import asyncio
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from flowgentic.backend_engines.base import BaseEngine


class QueuedEngine(BaseEngine):
	"""
	Wraps a backend engine with explicit queueing semantics.

	Unlike the direct await pattern (closed-loop with backpressure),
	this provides open-loop submission where callers don't block
	waiting for execution slots.
	"""

	def __init__(
		self,
		engine: BaseEngine,
		max_workers: int,
		observer: Optional[Callable[[Dict[str, Any]], None]] = None,
	):
		"""
		Args:
		    engine: The underlying execution engine (e.g., AsyncFlowEngine)
		    max_workers: Number of concurrent workers pulling from queue
		    observer: Optional callback for event emission
		"""
		super().__init__(observer=observer)
		self._engine = engine
		self._max_workers = max_workers
		self._queue: asyncio.Queue = asyncio.Queue()  # Unbounded buffer
		self._workers: List[asyncio.Task] = []
		self._running = False
		self._pending_futures: Dict[str, asyncio.Future] = {}

	async def start(self) -> None:
		"""Start the worker pool. Must be called before submitting tasks."""
		if self._running:
			return
		self._running = True
		self._workers = [
			asyncio.create_task(self._worker(worker_id=i))
			for i in range(self._max_workers)
		]

	async def stop(self) -> None:
		"""Stop the worker pool gracefully."""
		if not self._running:
			return
		self._running = False

		# Signal workers to stop
		for _ in self._workers:
			await self._queue.put(None)

		# Wait for workers to finish
		await asyncio.gather(*self._workers, return_exceptions=True)
		self._workers = []

	async def submit(
		self,
		func: Callable,
		*args,
		task_kwargs: Optional[Dict[str, Any]] = None,
		**kwargs,
	) -> asyncio.Future:
		"""
		Submit a task for execution (fire-and-forget).

		Returns immediately with a Future that will be resolved
		when the task completes. The caller can await the Future
		later if they need the result.

		Args:
		    func: The function to execute
		    *args: Positional arguments for the function
		    task_kwargs: Kwargs passed to the task wrapper
		    **kwargs: Keyword arguments for the function

		Returns:
		    asyncio.Future that will contain the result
		"""
		task_id = str(uuid.uuid4())
		task_name = getattr(func, "__name__", str(func))
		future: asyncio.Future = asyncio.get_event_loop().create_future()

		# Emit submit event (task enters queue)
		self.emit(
			{
				"event": "t_submit",
				"ts": time.perf_counter(),
				"task_id": task_id,
				"task_name": task_name,
				"queue_size": self._queue.qsize(),
			}
		)

		# Package the work item
		work_item = {
			"task_id": task_id,
			"task_name": task_name,
			"func": func,
			"args": args,
			"kwargs": kwargs,
			"task_kwargs": task_kwargs or {},
			"future": future,
			"submit_ts": time.perf_counter(),
		}

		# Non-blocking put (queue is unbounded)
		await self._queue.put(work_item)

		return future

	async def _worker(self, worker_id: int) -> None:
		"""
		Worker coroutine that pulls from queue and executes tasks.

		This is where the actual execution happens. Each worker:
		1. Pulls a work item from the queue (may block here)
		2. Emits t_dispatched event
		3. Executes via the underlying engine
		4. Emits t_complete event
		5. Resolves the Future
		"""
		while self._running:
			try:
				work_item = await self._queue.get()

				# None is the shutdown signal
				if work_item is None:
					break

				task_id = work_item["task_id"]
				task_name = work_item["task_name"]
				func = work_item["func"]
				args = work_item["args"]
				kwargs = work_item["kwargs"]
				task_kwargs = work_item["task_kwargs"]
				future = work_item["future"]
				submit_ts = work_item["submit_ts"]

				dispatch_ts = time.perf_counter()
				queue_delay = dispatch_ts - submit_ts

				# Emit dispatched event (worker picks up task)
				self.emit(
					{
						"event": "t_dispatched",
						"ts": dispatch_ts,
						"task_id": task_id,
						"task_name": task_name,
						"worker_id": worker_id,
						"queue_delay": queue_delay,
					}
				)

				try:
					# Execute via underlying engine
					result = await self._engine.execute_tool(
						func, *args, task_kwargs=task_kwargs, **kwargs
					)

					complete_ts = time.perf_counter()
					exec_time = complete_ts - dispatch_ts

					# Emit completion event
					self.emit(
						{
							"event": "t_complete",
							"ts": complete_ts,
							"task_id": task_id,
							"task_name": task_name,
							"worker_id": worker_id,
							"exec_time": exec_time,
							"total_latency": complete_ts - submit_ts,
						}
					)

					# Resolve the future
					if not future.done():
						future.set_result(result)

				except Exception as e:
					complete_ts = time.perf_counter()

					# Emit error event
					self.emit(
						{
							"event": "t_error",
							"ts": complete_ts,
							"task_id": task_id,
							"task_name": task_name,
							"worker_id": worker_id,
							"error": str(e),
						}
					)

					# Reject the future
					if not future.done():
						future.set_exception(e)

				finally:
					self._queue.task_done()

			except asyncio.CancelledError:
				break
			except Exception as e:
				# Log but don't crash the worker
				self.emit(
					{
						"event": "worker_error",
						"ts": time.perf_counter(),
						"worker_id": worker_id,
						"error": str(e),
					}
				)

	async def execute_tool(
		self,
		func: Callable,
		*args,
		task_kwargs: Optional[Dict[str, Any]] = None,
		**kwargs,
	) -> Any:
		"""
		Execute a tool through the queue and wait for result.

		This provides compatibility with the BaseEngine interface
		while still using the queueing semantics internally.
		"""
		future = await self.submit(func, *args, task_kwargs=task_kwargs, **kwargs)
		return await future

	def wrap_node(self, node_func: Callable):
		"""Delegate to underlying engine."""
		return self._engine.wrap_node(node_func)

	async def wait_for_completion(self) -> None:
		"""Wait for all queued tasks to complete."""
		await self._queue.join()

	@property
	def queue_size(self) -> int:
		"""Current number of items in queue."""
		return self._queue.qsize()

	@property
	def is_running(self) -> bool:
		"""Whether the worker pool is running."""
		return self._running
