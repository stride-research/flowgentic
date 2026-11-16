"""The host's own health: how late its event loop runs, and the collector's pauses."""

from __future__ import annotations

import asyncio
import gc
import time
from array import array

from flowgentic import _telemetry


class LoopLag:
	"""Samples how late the event loop wakes a 100 ms timer.

	``summary`` weighs each sample by its period, so a long block counts for its
	whole length rather than as one sample.
	"""

	INTERVAL = 0.1

	def __init__(self) -> None:
		self.samples = array("d")
		self._task: asyncio.Task | None = None

	async def _run(self) -> None:
		loop = asyncio.get_running_loop()
		record = _telemetry.LOOP_LAG.record
		while True:
			t = loop.time()
			await asyncio.sleep(self.INTERVAL)
			lag = max(loop.time() - t - self.INTERVAL, 0.0)
			self.samples.append(lag)
			record(lag)

	def start(self) -> None:
		self._task = asyncio.get_running_loop().create_task(self._run())

	def stop(self) -> None:
		if self._task is not None:
			self._task.cancel()
			self._task = None

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
	"""The cyclic collector's pauses, by generation, from ``gc.callbacks``."""

	def __init__(self) -> None:
		self.seconds = array("d")
		self.generations = array("b")
		self._t0 = 0.0

	def _callback(self, phase: str, info: dict) -> None:
		if phase == "start":
			self._t0 = time.perf_counter()
			return
		pause = time.perf_counter() - self._t0
		generation = info.get("generation", -1)
		self.seconds.append(pause)
		self.generations.append(generation)
		_telemetry.GC_PAUSE.record(pause, {_telemetry.GC_GENERATION: generation})

	def start(self) -> None:
		gc.callbacks.append(self._callback)

	def stop(self) -> None:
		if self._callback in gc.callbacks:
			gc.callbacks.remove(self._callback)

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
