"""The host's own health: how late its event loop runs, and the collector's pauses.

A process has one cyclic collector, and each event loop has one timer, however
many hosts run: hosts that overlap share them, each through a view of its own.
Each pause and each lag sample is recorded once into the metrics, and each
host's report still gets those of its own run.

The collector's callback only buffers each pause. It runs inside a collection,
which starts at whatever allocation crosses the threshold, in whichever thread
made it: an OpenTelemetry SDK's reader, holding a histogram's lock while it
builds a data point, included. Recording there would wait for ever on a lock
its own thread holds, and leave the collector off for the whole process. The
event loop records the buffered pauses instead, at each lag tick and when a
host stops.
"""

from __future__ import annotations

import asyncio
import contextvars
import gc
import threading
import time
from array import array
from collections import deque

from flowgentic import _telemetry

# Guards the views of the collector and of the timers. Never taken inside a
# collection, so a thread holding it may allocate.
_lock = threading.Lock()

_GENERATIONS = {g: {_telemetry.GC_GENERATION: g} for g in (-1, 0, 1, 2)}


class _Collector:
	"""The process's one collector callback, and the pauses it buffered."""

	def __init__(self) -> None:
		self.views: tuple[CollectorPauses, ...] = ()
		self._buffer: deque[tuple[float, int]] = deque()
		self._t0 = 0.0

	def callback(self, phase: str, info: dict) -> None:
		# Inside a collection: no lock, no recording (see the module's docstring).
		if phase == "start":
			self._t0 = time.perf_counter()
		else:
			pause = time.perf_counter() - self._t0
			self._buffer.append((pause, info.get("generation", -1)))

	def flush(self) -> None:
		"""Record the buffered pauses, each once, and give them to every view.

		Called on an event loop, never inside a collection.
		"""
		views = self.views
		record = _telemetry.GC_PAUSE.record
		# Only the pauses buffered now: recording allocates, and may add more.
		for _ in range(len(self._buffer)):
			try:
				pause, generation = self._buffer.popleft()
			except IndexError:  # another event loop took the rest
				return
			attributes = _GENERATIONS.get(generation)
			record(pause, attributes or {_telemetry.GC_GENERATION: generation})
			for view in views:
				view.seconds.append(pause)
				view.generations.append(generation)


_collector = _Collector()


class _Timer:
	"""One event loop's lag timer, shared by the hosts that run on the loop."""

	def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
		self.views: tuple[LoopLag, ...] = ()
		# In a context of its own: the timer may outlive the host that started it.
		self.task = loop.create_task(self._run(), context=contextvars.Context())

	async def _run(self) -> None:
		loop = asyncio.get_running_loop()
		record = _telemetry.LOOP_LAG.record
		while True:
			t = loop.time()
			await asyncio.sleep(LoopLag.INTERVAL)
			lag = max(loop.time() - t - LoopLag.INTERVAL, 0.0)
			record(lag)
			for view in self.views:
				view.samples.append(lag)
			_collector.flush()


_timers: dict[asyncio.AbstractEventLoop, _Timer] = {}


class LoopLag:
	"""How late the event loop woke a 100 ms timer, during one host's run.

	``summary`` weighs each sample by its period, so a long block counts for its
	whole length rather than as one sample.
	"""

	INTERVAL = 0.1

	def __init__(self) -> None:
		self.samples = array("d")
		self._loop: asyncio.AbstractEventLoop | None = None

	def start(self) -> None:
		loop = asyncio.get_running_loop()
		with _lock:
			timer = _timers.get(loop)
			if timer is None:
				timer = _timers[loop] = _Timer(loop)
			timer.views += (self,)
		self._loop = loop

	def stop(self) -> None:
		if self._loop is None:
			return
		with _lock:
			timer = _timers[self._loop]
			timer.views = tuple(v for v in timer.views if v is not self)
			if not timer.views:
				timer.task.cancel()
				del _timers[self._loop]
		self._loop = None

	def summary(self) -> dict:
		lag = list(self.samples)
		if not lag:
			return {"samples": 0}
		total = sum(self.INTERVAL + x for x in lag)

		def share_over(x: float) -> float:
			return sum(max(v - x, 0.0) for v in lag) / total

		lo, hi = 0.0, max(lag)
		if share_over(0.0) <= 0.01:
			p99 = 0.0
		else:
			for _ in range(50):
				mid = (lo + hi) / 2
				lo, hi = (mid, hi) if share_over(mid) > 0.01 else (lo, mid)
			p99 = hi
		return {
			"samples": len(lag),
			"max_ms": round(1e3 * max(lag), 1),
			"time_weighted_p99_ms": round(1e3 * p99, 1),
			"share_over_100ms": round(share_over(0.1), 4),
		}


class CollectorPauses:
	"""The cyclic collector's pauses during one host's run, by generation."""

	def __init__(self) -> None:
		self.seconds = array("d")
		self.generations = array("b")

	def start(self) -> None:
		with _lock:
			if not _collector.views:
				gc.callbacks.append(_collector.callback)
			_collector.views += (self,)

	def stop(self) -> None:
		with _lock:
			views = tuple(v for v in _collector.views if v is not self)
			if not views and _collector.callback in gc.callbacks:
				gc.callbacks.remove(_collector.callback)
			# This view's last pauses; after the last view, every pause.
			_collector.flush()
			_collector.views = views

	def summary(self) -> dict:
		out: dict = {
			"pauses": len(self.seconds),
			"total_s": round(sum(self.seconds), 3),
		}
		for g in (0, 1, 2):
			xs = [s for s, gen in zip(self.seconds, self.generations) if gen == g]
			if xs:
				out[f"gen{g}"] = {
					"count": len(xs),
					"total_s": round(sum(xs), 3),
					"max_ms": round(1e3 * max(xs), 1),
				}
		return out
