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

A view keeps running values, not every sample, so its memory stays the same
however long its host runs: a server's may run for days.
"""

from __future__ import annotations

import asyncio
import contextvars
import gc
import math
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
				view.add(pause, generation)


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
				view.add(lag)
			_collector.flush()


_timers: dict[asyncio.AbstractEventLoop, _Timer] = {}


class LoopLag:
	"""How late the event loop woke a 100 ms timer, during one host's run.

	``summary`` weighs each sample by its period, so a long block counts for its
	whole length rather than as one sample. The count, the maximum and the share
	of time over 100 ms are running values, exact. The time-weighted p99 comes
	from a histogram of the samples with a fixed number of buckets, each 1 %
	wider than the one below it from 0.1 ms up to a day, with each bucket's
	count and sum: it is within 1 % of the exact value, or 0.1 ms, whichever is
	larger (for a p99 under a day).
	"""

	INTERVAL = 0.1
	_OVER = 0.1  # the summary's share_over_100ms
	_FLOOR = 1e-4  # the lowest bucket holds the samples below 0.1 ms
	_RATIO = 1.01  # each bucket's upper edge over its lower one
	_LOG_RATIO = math.log(_RATIO)
	# Bucket i > 0 holds [FLOOR * RATIO**(i-1), FLOOR * RATIO**i), and the last
	# one every sample from its lower edge, over a day, up.
	_BUCKETS = math.ceil(math.log(86_400 / _FLOOR) / _LOG_RATIO) + 2

	def __init__(self) -> None:
		self.samples = 0
		self._time = 0.0  # the samples' periods: INTERVAL + lag each
		self._lag = 0.0  # the lags' sum
		self._over = 0.0  # the lags' sum over _OVER, less _OVER each
		self._max = 0.0
		self._counts = array("d", [0.0]) * self._BUCKETS
		self._sums = array("d", [0.0]) * self._BUCKETS
		self._loop: asyncio.AbstractEventLoop | None = None

	def add(self, lag: float) -> None:
		"""Count one sample: how late the timer woke, in seconds."""
		self.samples += 1
		self._time += self.INTERVAL + lag
		self._lag += lag
		if lag > self._OVER:
			self._over += lag - self._OVER
		if lag > self._max:
			self._max = lag
		if lag < self._FLOOR:
			i = 0
		else:
			i = min(
				1 + int(math.log(lag / self._FLOOR) / self._LOG_RATIO),
				self._BUCKETS - 1,
			)
		self._counts[i] += 1
		self._sums[i] += lag

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
		if not self.samples:
			return {"samples": 0}
		return {
			"samples": self.samples,
			"max_ms": round(1e3 * self._max, 1),
			"time_weighted_p99_ms": round(1e3 * self._p99(), 1),
			"share_over_100ms": round(self._over / self._time, 4),
		}

	def _p99(self) -> float:
		"""The lag the loop was later than for 1 % of the time: the x at which
		the samples' sum of max(lag - x, 0) is 1 % of their periods' sum.

		That sum falls as x rises, linearly between samples. It is exact at
		each bucket's edges, from the counts and sums of the buckets above,
		and between buckets with no sample; inside the bucket that holds the
		p99, it is taken as linear, so the error is below the bucket's width.
		"""
		target = 0.01 * self._time
		if self._lag <= target:  # the share over 0
			return 0.0
		count = 0.0  # samples in the buckets above the one at hand
		total = 0.0  # and their sum
		for i in range(self._BUCKETS - 1, -1, -1):
			c = self._counts[i]
			if not c:
				continue
			lo = 0.0 if i == 0 else self._FLOOR * self._RATIO ** (i - 1)
			if i == self._BUCKETS - 1:
				hi = self._max
			else:
				hi = min(self._FLOOR * self._RATIO**i, self._max)
			if count and total - count * hi > target:
				# Between this bucket and the one above, where no sample is.
				return (total - target) / count
			at_lo = total + self._sums[i] - (count + c) * lo
			if at_lo > target:
				at_hi = max(total - count * hi, 0.0)
				return lo + (hi - lo) * (at_lo - target) / (at_lo - at_hi)
			count += c
			total += self._sums[i]
		# Below the lowest sample; 0 if rounding put the share over 0 at 1 %.
		return max((total - target) / count, 0.0)


class CollectorPauses:
	"""The cyclic collector's pauses during one host's run, by generation: the
	count, total and maximum of each, as running values."""

	def __init__(self) -> None:
		self.pauses = 0
		self.total = 0.0
		# For generations 0, 1 and 2: [count, total, maximum].
		self._generations = {g: [0, 0.0, 0.0] for g in (0, 1, 2)}

	def add(self, pause: float, generation: int) -> None:
		"""Count one pause of ``generation``, in seconds."""
		self.pauses += 1
		self.total += pause
		g = self._generations.get(generation)
		if g is not None:
			g[0] += 1
			g[1] += pause
			if pause > g[2]:
				g[2] = pause

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
		out: dict = {"pauses": self.pauses, "total_s": round(self.total, 3)}
		for g, (count, total, most) in self._generations.items():
			if count:
				out[f"gen{g}"] = {
					"count": count,
					"total_s": round(total, 3),
					"max_ms": round(1e3 * most, 1),
				}
		return out
