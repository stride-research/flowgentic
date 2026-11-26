"""The model fleet's client: one transport per model server replica.

Agents' model calls go to placed models, one replica per GPU or server. The
fleet decides, for each call, which replica takes it and when:

- **Routing.** ``AgentAffinity`` sends each agent to one replica by a stable
  hash of its id, the same in every process (with ``process=``, among the
  replicas its process sends to), so the agent's growing prompt stays in that
  replica's prefix cache. ``RoundRobin`` and ``LeastLoaded`` are
  the baselines to compare it with.
- **Admission.** ``TokenAdmission`` admits requests to a replica while the
  tokens of live context in flight stay under one cap and the requests in
  flight under another. Past the point where a server's KV cache holds its
  agents' contexts, its prefix cache evicts the contexts about to be reused and
  every call recomputes its history; admission keeps the server below that
  point and lets the other agents wait. ``RequestAdmission`` counts requests
  only. Admission is first come, first served, so a long context is never
  overtaken forever. Without admission (``None``), every call goes at once.
  Another policy is an ``Admission`` whose gates follow the ``Gate`` protocol:
  a gate sees whose call it admits, and hears when an agent ends
  (``Fleet.agent_done``).
- **Sharing across processes.** Each agent process gets ``share`` of every
  replica's caps, rounded down. This holds because every process routes by
  the same hash. It is a static split. With ``process=(index, processes)``
  instead, the processes split each replica's caps exactly, so that their
  parts add up to the cap; a process may hold none of a replica, and then
  sends it nothing. By default each process takes its part from every
  replica; with ``pack=True``, from as few replicas as hold it, in order, so
  that it holds whole caps where the numbers allow and keeps connections to
  a few servers. A process routes each agent to one of the replicas it holds
  a part of, in proportion to its parts.
- **Recovery.** With ``Recovery``, a call whose server went away (a connection
  error, a timeout, a 5xx, a 401 after a new key) releases its place, waits for
  the replica to come back, and is sent again; ``outage_s`` after its first
  failure it fails, even if the replica came back and the call kept failing.
  A check that raises or hangs counts as the replica still being down.
- **Accounting.** One ``CallRecord`` per call goes to the listeners: the wait
  for admission, the time at the server, the time spent waiting out an
  outage, the tokens, the prompt tokens the server found in its prefix cache
  and the reply's reasoning tokens when the server reports them, the gap since
  the agent's previous call, the tool whose result came just before, and the
  caller's ``meta``. The fleet itself keeps counters.
- **Metrics.** The call's whole time is an OpenTelemetry histogram, one per
  call; the wait for admission and the time at the replica are histograms
  recorded for each try, a failed one with its ``error.type``. The requests
  sent again are counted, and each outage ``Recovery`` watched is a
  histogram's value, by how it ended. Each replica's tokens and requests in
  flight, and its requests waiting, are gauges. They carry the fleet's
  ``name``, so the fleets of several models in one process stay apart, and
  the replica's index.

A request's live context is estimated before it is sent: the agent's size
after its previous call, as the server counted it, plus the characters added
since, at the tokens per character the server's counts give, plus a reserve
for the reply and the call's own ``reserve``. The size leaves out the previous
reply's reasoning tokens, when the server reports them
(``output_token_details.reasoning``): the conversation does not keep a model's
thinking. An agent's first call is estimated from the sizes the server counted
for earlier first calls, as a fixed part, such as tool schemas and a template
that the messages do not show, plus tokens per character, never fewer than the
characters later calls add count.

**Replicas are transports** (``Transport``): each sends a conversation, as
OpenAI Chat Completions messages, with the tools' JSON schemas, to one server,
and returns a ``Reply``: its content and tool calls, the usage, and the
reasoning text kept apart from the conversation. ``HTTPTransport``, the
default, calls an OpenAI-compatible server (vLLM, SGLang, OpenRouter) over
httpx; ``OpenAITransport`` calls one with the ``openai`` SDK.
A LangChain chat model given as a replica, or returned by ``Recovery``'s
check, runs through LangChain's adapter (``flowgentic.langchain``), which
also takes LangChain messages and tools as they are.

``fleet.ainvoke(agent_id, messages, tools=...)`` is the direct path; it
returns the ``Reply``. ``fleet.chat_model()`` is the fleet as a LangChain chat
model, for LangGraph graphs and prebuilt agents (``flowgentic.langchain``).
Both take, per call, settings for the replica (``extra_body``,
``max_tokens``, ...), which reach the replica for that call only, a
``reserve`` of tokens beyond the usual reply, such as a thinking budget, and
a ``meta`` that the call's record carries. Which settings make a model think
is the model's business, not the fleet's.

**Each call is a GenAI ``chat`` span**, from routing to the reply, its wait
for admission, tries and outages included, with the request's and the
response's model, the server's address and port, the finish reasons, the
usage the server reported (input, output, cached input and reasoning tokens),
the agent's id as ``gen_ai.conversation.id``, and the call's own: its wait for
admission, the requests sent again and the time spent on them, the gap since
the agent's previous call and the tool before. With the LangChain adapter,
and OpenTelemetry's LangChain instrumentation on, the instrumentation's span
is the call's, and the fleet records none, so no usage is counted twice.
"""

from __future__ import annotations

import asyncio
import bisect
import contextlib
import contextvars
import inspect
import itertools
import json
import logging
import operator
import time
import weakref
import zlib
from collections import Counter, OrderedDict, deque
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from itertools import accumulate
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from opentelemetry.trace import INVALID_SPAN, SpanKind, Status, StatusCode

from flowgentic import _agents, _telemetry
from flowgentic.fleet._transport import (
	AuthenticationFailed,
	HTTPTransport,
	InvalidToolCall,
	ModelServerError,
	OpenAITransport,
	Reply,
	RequestRejected,
	ServerUnavailable,
	ToolCall,
	Transport,
	Usage,
	parse_completion,
)

log = logging.getLogger(__name__)

__all__ = [
	"Admission",
	"AdmissionGate",
	"AgentAffinity",
	"AuthenticationFailed",
	"CallRecord",
	"Fleet",
	"Gate",
	"HTTPTransport",
	"InvalidToolCall",
	"LeastLoaded",
	"ModelServerError",
	"OpenAITransport",
	"Recovery",
	"Reply",
	"ReplicaLoad",
	"RequestAdmission",
	"RequestRejected",
	"RoundRobin",
	"Routing",
	"ServerUnavailable",
	"Share",
	"TokenAdmission",
	"ToolCall",
	"Transport",
	"Usage",
	"parse_completion",
]


def _stable_hash(key: str) -> int:
	"""The same in every process, unlike ``hash()``."""
	return zlib.crc32(key.encode())


# --- admission ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReplicaLoad:
	"""One replica's admission state."""

	tokens: int
	requests: int
	waiting: int


@runtime_checkable
class Gate(Protocol):
	"""One replica's admission, in one process.

	Before the fleet sends an agent's call to the replica, it awaits
	``acquire(n, agent_id)``, where ``n`` is the call's estimated tokens of live
	context, or 0 when the admission does not count tokens. Once the call is
	over, whether it succeeded, failed or was cancelled, the fleet calls
	``release(n, agent_id)`` with the same arguments. A call that waits out a
	recovery releases before it waits and acquires again before it is sent
	again. If ``acquire`` is cancelled, it must leave the gate as it found it,
	even when it had admitted the call before the cancellation reached it.

	``agent_done(agent_id)`` says that an agent ended (``Fleet.agent_done``), so
	a gate that keeps something per agent can drop it; a call of that agent may
	still be in flight. ``load()`` and ``stats()`` feed the fleet's ``load()``,
	``stats()`` and metrics.
	"""

	async def acquire(self, n: int, agent_id: str) -> None: ...

	def release(self, n: int, agent_id: str) -> None: ...

	def agent_done(self, agent_id: str) -> None: ...

	def load(self) -> ReplicaLoad: ...

	def stats(self) -> dict: ...


class _OversizeWarning:
	"""Logs the first request admitted alone above a token cap, once for all
	the gates that share it: a fleet's gates share one, and a gate built on its
	own has its own."""

	__slots__ = ("fleet", "logged")

	def __init__(self, fleet: str | None = None) -> None:
		self.fleet = fleet
		self.logged = False

	def __call__(self, n: int, cap: int) -> None:
		if self.logged:
			return
		self.logged = True
		log.warning(
			"%sa model request of %d tokens is larger than this process's token "
			"cap at its replica, %d, so it is admitted alone; it takes the "
			"replica past its cap while the other processes use their shares. "
			"Raise the cap or this process's share so that the largest context "
			"fits in one share. Later requests like it are counted in the gates' "
			"stats (oversize), without a warning",
			"" if not self.fleet else f"fleet {self.fleet!r}: ",
			n,
			cap,
		)


class AdmissionGate:
	"""Admits requests to one replica while the tokens of live context and the
	requests in flight stay under their caps. ``tokens=None`` counts requests
	only. First come, first served, whatever the agent; a request larger than
	the token cap is admitted alone.

	A gate holds one process's share of a replica's caps, and cannot see the
	other processes' requests. A request admitted alone above the share's token
	cap therefore takes the replica past its cap while the other processes use
	their shares: such requests are counted (``oversize`` in ``stats()``), and
	the first is logged as a warning, once for all the gates of a fleet. A
	``Gate``."""

	def __init__(self, tokens: int | None, requests: int) -> None:
		if (tokens is not None and tokens < 1) or requests < 1:
			raise ValueError("the admission caps must be at least 1")
		self.cap_tokens = tokens
		self.cap_requests = requests
		self.tokens = 0
		self.requests = 0
		self.tokens_max = 0
		self.requests_max = 0
		self.admitted = 0
		self.queued = 0
		self.oversize = 0
		self.blocked_s = 0.0
		self._warn_oversize = _OversizeWarning()  # a fleet gives its gates one
		self._blocked_since: float | None = None
		self._waiters: deque[tuple[int, asyncio.Future]] = deque()

	def _fits(self, n: int) -> bool:
		if self.requests == 0:
			return True
		if self.requests >= self.cap_requests:
			return False
		return self.cap_tokens is None or self.tokens + n <= self.cap_tokens

	def _take(self, n: int) -> None:
		if self.cap_tokens is not None and n > self.cap_tokens:
			self.oversize += 1
			self._warn_oversize(n, self.cap_tokens)
		self.tokens += n
		self.requests += 1
		self.admitted += 1
		self.tokens_max = max(self.tokens_max, self.tokens)
		self.requests_max = max(self.requests_max, self.requests)

	async def acquire(self, n: int, agent_id: str) -> None:
		"""Wait until a request of ``n`` tokens is admitted."""
		if not self._waiters and self._fits(n):
			self._take(n)
			return
		future = asyncio.get_running_loop().create_future()
		if not self._waiters:
			self._blocked_since = time.monotonic()
		entry = (n, future)
		self._waiters.append(entry)
		self.queued += 1
		try:
			await future
		except asyncio.CancelledError:
			if future.done() and not future.cancelled():
				self.release(n, agent_id)  # admitted, then cancelled before it ran
			else:
				try:
					self._waiters.remove(entry)
				except ValueError:
					pass
				self._wake()
			raise

	def release(self, n: int, agent_id: str) -> None:
		"""Release an admitted request of ``n`` tokens."""
		self.tokens -= n
		self.requests -= 1
		self._wake()

	def agent_done(self, agent_id: str) -> None:
		"""Nothing to drop: the gate keeps nothing per agent."""

	def _wake(self) -> None:
		while self._waiters and self._fits(self._waiters[0][0]):
			n, future = self._waiters.popleft()
			if future.done():
				continue
			self._take(n)
			future.set_result(None)
		if not self._waiters and self._blocked_since is not None:
			self.blocked_s += time.monotonic() - self._blocked_since
			self._blocked_since = None

	def load(self) -> ReplicaLoad:
		"""Tokens and requests in flight, and requests waiting."""
		return ReplicaLoad(self.tokens, self.requests, len(self._waiters))

	def stats(self) -> dict:
		"""Caps, counts, peaks, and the time some request waited.
		``oversize`` counts the requests admitted alone above the token cap."""
		blocked = self.blocked_s
		if self._blocked_since is not None:
			blocked += time.monotonic() - self._blocked_since
		return {
			"cap_tokens": self.cap_tokens,
			"cap_requests": self.cap_requests,
			"admitted": self.admitted,
			"queued": self.queued,
			"oversize": self.oversize,
			"tokens_in_flight_max": self.tokens_max,
			"requests_in_flight_max": self.requests_max,
			"blocked_s": round(blocked, 3),
		}


class _Open:
	"""No admission: every call goes at once."""

	tokens = requests = 0

	async def acquire(self, n: int, agent_id: str) -> None:
		self.requests += 1

	def release(self, n: int, agent_id: str) -> None:
		self.requests -= 1

	def agent_done(self, agent_id: str) -> None:
		pass

	def load(self) -> ReplicaLoad:
		return ReplicaLoad(0, self.requests, 0)

	def stats(self) -> dict:
		return {"cap_tokens": None, "cap_requests": None}


class _Unused:
	"""A replica this process holds none of (``Fleet(process=...)``): the
	fleet sends it nothing."""

	__slots__ = ("cap_tokens",)

	def __init__(self, counts_tokens: bool) -> None:
		self.cap_tokens = 0 if counts_tokens else None

	async def acquire(self, n: int, agent_id: str) -> None:
		raise RuntimeError("this process holds none of this replica's caps")

	def release(self, n: int, agent_id: str) -> None:
		pass

	def agent_done(self, agent_id: str) -> None:
		pass

	def load(self) -> ReplicaLoad:
		return ReplicaLoad(0, 0, 0)

	def stats(self) -> dict:
		return {"cap_tokens": self.cap_tokens, "cap_requests": 0}


class Share(float):
	"""A process's share of one replica's caps, as ``Admission.gate`` gets it
	from a fleet made with ``process=(index, processes)``.

	The fleet cuts each replica's caps into ``units`` equal parts, and this
	process holds those from ``start`` to ``stop``. As a float, a share is the
	fraction of the caps it holds, which is what ``Fleet(share=...)`` gives
	``gate``. ``part(cap)`` is its exact part of a cap: the parts of the
	processes that use a replica add up to the cap, and none is above it."""

	__slots__ = ("start", "stop", "units")

	start: int
	stop: int
	units: int

	def __new__(cls, start: int, stop: int, units: int) -> Share:
		start, stop, units = (operator.index(x) for x in (start, stop, units))
		if not 0 <= start <= stop <= units or units < 1:
			raise ValueError(
				"a share needs 0 <= start <= stop <= units, and units >= 1"
			)
		share = super().__new__(cls, (stop - start) / units)
		share.start, share.stop, share.units = start, stop, units
		return share

	def part(self, cap: int) -> int:
		"""This process's part of ``cap``, cut where the other processes' parts
		are cut."""
		return self.stop * cap // self.units - self.start * cap // self.units

	def __repr__(self) -> str:
		return f"Share(start={self.start}, stop={self.stop}, units={self.units})"

	def __reduce__(self) -> tuple:
		return (Share, (self.start, self.stop, self.units))


class Admission(Protocol):
	"""Makes one gate per replica, for a process's ``share`` of its caps.
	``counts_tokens`` says whether its gates are given each call's estimated
	tokens, or 0.

	``share`` is a float, the fraction of the caps: the one given to
	``Fleet(share=...)``, or, from a fleet made with ``process=...``, a
	``Share``, whose ``part(cap)`` gives the process its exact part of a cap.
	Such a fleet cuts each replica into as many units as the admission's
	``requests`` says, if it has that attribute, or else one unit per process,
	and makes a gate only for the replicas this process holds a part of."""

	counts_tokens: bool

	def gate(self, share: float) -> Gate: ...


def _share(cap: int, share: float) -> int:
	# A cap below 1 is a mistake, not a cap of 1: refuse it before the share
	# is rounded up.
	if cap < 1:
		raise ValueError("the admission caps must be at least 1")
	if isinstance(share, Share):
		return share.part(cap)
	return max(1, int(cap * share))


@dataclass(frozen=True, slots=True)
class TokenAdmission:
	"""Caps per replica, across all agent processes: tokens of live context in
	flight, and requests in flight."""

	tokens: int
	requests: int
	counts_tokens = True

	def gate(self, share: float) -> AdmissionGate:
		tokens = _share(self.tokens, share)
		requests = _share(self.requests, share)
		if tokens < 1 and requests >= 1:
			raise ValueError(
				f"a token cap of {self.tokens} leaves this process none of it at a "
				f"replica where it holds {requests} of {self.requests} requests: "
				"make the token cap at least the request cap"
			)
		return AdmissionGate(tokens, requests)


@dataclass(frozen=True, slots=True)
class RequestAdmission:
	"""A cap per replica, across all agent processes, on requests in flight."""

	requests: int
	counts_tokens = False

	def gate(self, share: float) -> AdmissionGate:
		return AdmissionGate(None, _share(self.requests, share))


def _parts(
	index: int, processes: int, replicas: int, units: int, pack: bool
) -> dict[int, Share]:
	"""Process ``index``'s shares of the replicas it uses, by replica.

	The fleet's ``replicas × units`` units are cut into ``processes`` runs, in
	order, which differ by one unit at most. Packed, the units run replica by
	replica, so a run takes as few replicas as hold it. Spread, they run
	across the replicas, a unit of each in turn, so a run takes its units from
	as many replicas as it can, and from each nearly as many. Either way, the
	runs cut each replica's units into consecutive parts."""
	total = replicas * units
	if total < processes:
		raise ValueError(
			f"{processes} processes cannot each hold at least one request of "
			f"{replicas} replicas of {units} requests: use fewer processes, more "
			"replicas or a larger request cap"
		)
	lo = index * total // processes
	hi = (index + 1) * total // processes
	parts: dict[int, Share] = {}
	if pack:
		for r in range(lo // units, (hi - 1) // units + 1):
			base = r * units
			parts[r] = Share(max(lo, base) - base, min(hi, base + units) - base, units)
		return parts
	for r in range(replicas):
		# Replica r's units are the fleet's r, r + replicas, r + 2 replicas, ...
		start, stop = -((r - lo) // replicas), -((r - hi) // replicas)
		if stop > start:
			parts[r] = Share(start, stop, units)
	return parts


# --- routing -----------------------------------------------------------------------


class Routing(Protocol):
	"""Chooses the replica for an agent's call. ``loads`` reads a replica's
	load from its gate when the routing first asks for it, so a routing that
	reads none, such as ``AgentAffinity``, costs no reads.

	``loads`` holds the replicas this process sends to, which are all of them
	unless the fleet was made with ``process=...``; the fleet takes the
	routing's choice as an index into them. Its ``weights`` are then this
	process's part of each one's request cap, where the parts differ, and
	``None`` where they do not."""

	def choose(self, agent_id: str, loads: Sequence[ReplicaLoad]) -> int: ...


class _Loads(Sequence):
	"""The replicas' loads for one call, each read from its gate once, when
	first asked for, and this process's weights for them."""

	__slots__ = ("_gates", "_read", "weights", "_cum")

	def __init__(
		self,
		gates: list[Gate],
		weights: tuple[int, ...] | None = None,
		cum: tuple[int, ...] | None = None,
	) -> None:
		self._gates = gates
		self._read: list[ReplicaLoad | None] = [None] * len(gates)
		self.weights = weights
		self._cum = cum  # the weights' running sums

	def __len__(self) -> int:
		return len(self._gates)

	def __getitem__(self, i: int | slice) -> ReplicaLoad | list[ReplicaLoad]:
		if isinstance(i, slice):
			return [self[j] for j in range(*i.indices(len(self)))]
		load = self._read[i]
		if load is None:
			load = self._read[i] = self._gates[i].load()
		return load

	def __iter__(self) -> Iterator[ReplicaLoad]:
		self._read = [
			load if load is not None else gate.load()
			for load, gate in zip(self._read, self._gates)
		]
		return iter(self._read)


class AgentAffinity:
	"""Each agent always to the same replica, by a stable hash of its id; in
	proportion to the process's parts of the replicas where they differ."""

	def choose(self, agent_id: str, loads: Sequence[ReplicaLoad]) -> int:
		cum = getattr(loads, "_cum", None)
		if cum is None:
			return _stable_hash(agent_id) % len(loads)
		return bisect.bisect_right(cum, _stable_hash(agent_id) % cum[-1])


class RoundRobin:
	"""Calls to the replicas in turn, whatever the agent."""

	def __init__(self) -> None:
		self._next = itertools.count()

	def choose(self, agent_id: str, loads: Sequence[ReplicaLoad]) -> int:
		return next(self._next) % len(loads)


class LeastLoaded:
	"""Each call to the replica with the fewest requests in flight and waiting."""

	def choose(self, agent_id: str, loads: Sequence[ReplicaLoad]) -> int:
		best, chosen = None, 0
		for i, load in enumerate(loads):  # each load read once
			key = (load.requests + load.waiting, load.tokens)
			if best is None or key < best:
				best, chosen = key, i
		return chosen


# --- recovery ----------------------------------------------------------------------

_TRANSIENT = frozenset(
	{
		"APIConnectionError",
		"APITimeoutError",
		"InternalServerError",
		"AuthenticationError",
		"RateLimitError",
		"ConflictError",
	}
)
"""Errors of a server that is down, restarting, overloaded or busy, or that came
back with a new key, by the OpenAI client's class names. Matched along the
class's MRO, so langchain-openai's own wrappers of them match too."""


def _transient(exc: BaseException) -> bool:
	"""Whether ``exc`` is a server's that is down, restarting, overloaded or
	busy, or came back with a new key: a transient ``ModelServerError``, or
	one of the openai SDK's, by name or, for a 408, which has no class of its
	own, by the status of an ``APIStatusError``."""
	if isinstance(exc, ModelServerError):
		return exc.transient
	names = {c.__name__ for c in type(exc).__mro__}
	if names & _TRANSIENT:
		return True
	return "APIStatusError" in names and getattr(exc, "status_code", None) == 408


class Recovery:
	"""Waits out a replica whose server went away, for at most ``outage_s``.

	``check(i)``, an ``async def``, returns replica ``i``'s transport once its
	server serves again (a LangChain chat model is wrapped as one), a new one if
	its endpoint or key changed, or ``None`` while it is down. The first call
	that fails on a replica starts one watch, which calls ``check`` every
	``poll_s`` seconds until the replica serves or ``outage_s`` has passed; later calls wait on the same watch. A call whose
	watch gives up while at least ``poll_s`` of the call's own ``outage_s`` is
	left starts a new one. A check that raises, or does not answer within
	``check_timeout_s``, counts as the replica still being down. The watch
	serves every call on the replica, so it runs in a context of its own, not
	in the first failing call's. A call that fails again after its replica
	came back waits ``poll_s`` before it is sent again. A call fails
	``outage_s`` after its first failure, or earlier when its watch gives up
	with less than ``poll_s`` of that left.
	"""

	def __init__(
		self,
		check: Callable[[int], Awaitable[Any | None]],
		*,
		outage_s: float = 1_800.0,
		poll_s: float = 5.0,
		check_timeout_s: float = 30.0,
	) -> None:
		if outage_s < 0 or poll_s <= 0 or check_timeout_s <= 0:
			raise ValueError(
				"outage_s must not be negative, and poll_s and check_timeout_s "
				"must be more than 0"
			)
		self.check = check
		self.outage_s = outage_s
		self.poll_s = poll_s
		self.check_timeout_s = check_timeout_s
		self.outages: Counter[int] = Counter()
		self.gave_up: Counter[int] = Counter()
		self.down_s: Counter[int] = Counter()
		self.check_failures: Counter[int] = Counter()
		self._watches: dict[int, asyncio.Future] = {}
		self._tasks: set[asyncio.Task] = set()

	async def wait(self, i: int, timeout: float | None = None) -> Any | None:
		"""Replica ``i``'s transport once it serves again, or ``None`` after
		``outage_s``, or after ``timeout`` seconds for this caller.

		A caller with a ``timeout`` waits it out: if the watch it joined gives
		up first, having started earlier for another call, and at least
		``poll_s`` of the caller's time is left, the caller starts a new watch.
		With less left, it returns ``None`` then, and leaves no watch behind
		that no call waits for."""
		return await self._wait(i, timeout, {_telemetry.FLEET_REPLICA: i})

	async def _wait(
		self, i: int, timeout: float | None, attributes: dict[str, Any]
	) -> Any | None:
		"""``wait``, with the attributes of the outage a watch it starts
		records: the fleet's name and the replica's index."""
		loop = asyncio.get_running_loop()
		end = None if timeout is None else loop.time() + timeout
		while True:
			if end is not None and end - loop.time() <= 0:
				return None
			future = self._watches.get(i)
			if future is None:
				future = self._watches[i] = loop.create_future()
				# No agent, span or run config of the call that happened to fail
				# first.
				task = asyncio.create_task(
					self._watch(i, future, attributes), context=contextvars.Context()
				)
				self._tasks.add(task)
				task.add_done_callback(self._tasks.discard)
			left = None if end is None else end - loop.time()
			# asyncio.wait leaves the watch running when this caller times out
			# or is cancelled.
			done, _ = await asyncio.wait({future}, timeout=left)
			if not done:
				return None
			model = future.result()
			if model is not None or end is None:
				return model
			# The watch gave up a poll_s after its last check, so a new one may
			# check at once. With outage_s 0, a watch gives up without a check.
			if self.outage_s <= 0 or end - loop.time() < self.poll_s:
				return None

	def _ended(self, attributes: dict[str, Any], outcome: str, down_s: float) -> None:
		"""Record an outage that ended: ``recovered`` or ``gave_up``."""
		_telemetry.OUTAGE_DURATION.record(
			down_s, {**attributes, _telemetry.OUTAGE_OUTCOME: outcome}
		)

	async def _watch(
		self, i: int, future: asyncio.Future, attributes: dict[str, Any]
	) -> None:
		t0 = time.monotonic()
		failures = 0
		try:
			while True:
				left = self.outage_s - (time.monotonic() - t0)
				if left <= 0:
					down_s = time.monotonic() - t0
					self.gave_up[i] += 1
					self.down_s[i] += down_s
					self._ended(attributes, "gave_up", down_s)
					future.set_result(None)
					return
				try:
					timeout = min(self.check_timeout_s, left)
					model = await asyncio.wait_for(self.check(i), timeout)
				except Exception as exc:  # a timeout too: still down
					model = None
					failures += 1
					self.check_failures[i] += 1
					log.log(
						logging.WARNING if failures == 1 else logging.DEBUG,
						"replica %d: its check failed (%s: %s); still down",
						i,
						type(exc).__name__,
						exc,
					)
				if model is not None:
					down_s = time.monotonic() - t0
					self.outages[i] += 1
					self.down_s[i] += down_s
					self._ended(attributes, "recovered", down_s)
					future.set_result(model)
					return
				await asyncio.sleep(self.poll_s)
		finally:
			# The watch was cancelled: its calls see the replica as still down,
			# and those with at least poll_s left start a new watch.
			if not future.done():
				future.set_result(None)
			if self._watches.get(i) is future:
				del self._watches[i]

	def stats(self) -> dict:
		"""Outages per replica: waited out, their time down, given up, and the
		checks that raised or timed out."""
		return {
			"outage_s": self.outage_s,
			"outages": dict(self.outages),
			"down_s": {i: round(s, 1) for i, s in self.down_s.items()},
			"gave_up": dict(self.gave_up),
			"check_failures": dict(self.check_failures),
		}


# --- the fleet ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CallRecord:
	"""One model call.

	``wait_s`` is the time the call waited for admission, over all its tries;
	``service_s`` the time the try that succeeded spent at the replica; and
	``recovery_s`` the rest, when ``Recovery`` waited out an outage: the tries
	that failed, and the wait for the replica to come back. The three add up to
	the call's time."""

	agent_id: str
	replica: int
	t_end: float
	wait_s: float
	service_s: float
	prompt_tokens: int
	cached_tokens: int | None
	completion_tokens: int
	estimate: int
	gap_s: float | None
	after_tool: str | None
	prev_prompt_tokens: int | None
	reasoning_tokens: int | None = None
	"""The reply's thinking, if the server reports it."""
	meta: Any = None
	"""What the caller passed with the call."""
	recovery_s: float = 0.0
	"""The tries that failed and the wait for the replica to come back."""


def _message_chars(messages: Sequence[Any]) -> int:
	"""Characters of a conversation: contents and tool calls. Messages are
	OpenAI-format dicts, or LangChain messages through LangChain's adapter."""
	n = 0
	for m in messages:
		if isinstance(m, dict):
			c = m.get("content")
			if c is not None:
				n += len(c) if isinstance(c, str) else len(str(c))
			for call in m.get("tool_calls") or ():
				function = call.get("function") or {}
				n += len(function.get("name") or "") + len(
					function.get("arguments") or ""
				)
			continue
		c = m.content
		n += len(c) if isinstance(c, str) else len(str(c))
		for call in getattr(m, "tool_calls", None) or ():
			n += len(call.get("name", "")) + len(json.dumps(call.get("args", {})))
	return n


def _reply_chars(reply: Reply) -> int:
	"""Characters of a reply as the conversation keeps it."""
	n = len(reply.content)
	for call in reply.tool_calls:
		n += len(call.name) + len(json.dumps(call.args))
	for call in reply.invalid_tool_calls:
		n += len(call.name) + 2
	return n


def _after_tool(messages: Sequence[Any]) -> str | None:
	"""The tool whose result is the conversation's last message, if it is one."""
	if not messages:
		return None
	last = messages[-1]
	if not isinstance(last, dict):  # a LangChain message
		return last.name if getattr(last, "type", None) == "tool" else None
	if last.get("role") != "tool":
		return None
	if last.get("name"):
		return last["name"]
	call_id = last.get("tool_call_id")
	for m in reversed(messages):
		if isinstance(m, dict) and m.get("role") == "assistant":
			for call in m.get("tool_calls") or ():
				if call.get("id") == call_id:
					return (call.get("function") or {}).get("name")
			return None
	return None


def _as_transport(replica: object) -> Any:  # noqa: ANN401
	"""``replica`` as a transport: itself if it is one; a LangChain chat model,
	or anything else with ``ainvoke``, through LangChain's adapter."""
	if callable(getattr(replica, "complete", None)):
		return replica
	if callable(getattr(replica, "ainvoke", None)):
		from flowgentic.langchain import ChatModelTransport

		return ChatModelTransport(replica)
	raise TypeError(
		f"a replica must be a Transport (with an async complete()) or a LangChain "
		f"chat model, not {type(replica).__name__}"
	)


def _same_replica(model: object, transport: object) -> bool:
	"""Whether ``model``, as ``Recovery``'s check returned it, is the transport
	the fleet holds, or the chat model inside it."""
	return model is transport or model is getattr(transport, "chat_model", None)


_closing: set[asyncio.Task] = set()


def _close_later(transport: object) -> None:
	"""Close a replica's transport that ``Recovery`` replaced, if it can be
	closed (``aclose``), once its calls in flight have ended: its connection
	pools and their SSL context would otherwise stay with the process."""
	close = getattr(transport, "aclose", None)
	if not callable(close):
		return
	try:
		closing = close()  # a transport of one's own may close at once
	except Exception as exc:
		log.warning("closing a replaced replica failed: %r", exc)
		return
	if not inspect.isawaitable(closing):
		return
	task = asyncio.ensure_future(closing)
	_closing.add(task)

	def done(task: asyncio.Task) -> None:
		_closing.discard(task)
		if not task.cancelled() and task.exception() is not None:
			log.warning("closing a replaced replica failed: %r", task.exception())

	task.add_done_callback(done)


def _server(base_url: str | None) -> tuple[str | None, int | None]:
	if not base_url:
		return None, None
	parts = urlsplit(base_url)
	port = parts.port or {"http": 80, "https": 443}.get(parts.scheme)
	return parts.hostname, port


def _server_attributes(transport: object) -> dict[str, Any]:
	"""The ``server.address`` and ``server.port`` of a transport's
	``base_url``, if it has one."""
	host, port = _server(getattr(transport, "base_url", None))
	if not host:
		return {}
	if not port:
		return {_telemetry.SERVER_ADDRESS: host}
	return {_telemetry.SERVER_ADDRESS: host, _telemetry.SERVER_PORT: port}


def _chat_span(transport: object, attributes: dict | None) -> Any:  # noqa: ANN401
	"""A GenAI ``chat`` span for a call to ``transport``, to enter; a span
	that records nothing when the transport records its own
	(``records_span``)."""
	if not getattr(transport, "records_span", True):
		return contextlib.nullcontext(INVALID_SPAN)
	model = getattr(transport, "model", None)
	attrs: dict[str, Any] = {_telemetry.GEN_AI_OPERATION_NAME: _telemetry.CHAT}
	if model:
		attrs[_telemetry.GEN_AI_REQUEST_MODEL] = model
	attrs.update(_server_attributes(transport))
	if attributes:
		attrs.update(attributes)
	name = f"{_telemetry.CHAT} {model}" if model else _telemetry.CHAT
	return _telemetry.tracer.start_as_current_span(
		name,
		kind=SpanKind.CLIENT,
		attributes=attrs,
		record_exception=False,
		set_status_on_exception=False,
	)


def _renamed(span: Any, old: object, new: object) -> None:  # noqa: ANN401
	"""Name, on a call's ``chat`` span, the replica that ``Recovery`` put in
	place of ``old``: its model and its server. A replica without a model or
	a ``base_url`` leaves the old one's on the span, since a span cannot drop
	an attribute."""
	model = getattr(new, "model", None)
	if model and model != getattr(old, "model", None):
		span.update_name(f"{_telemetry.CHAT} {model}")
		span.set_attribute(_telemetry.GEN_AI_REQUEST_MODEL, model)
	span.set_attributes(_server_attributes(new))


def _failed(span: Any, exc: BaseException) -> str:  # noqa: ANN401
	"""Mark a ``chat`` span failed with ``exc``; return its ``error.type``."""
	error = type(exc).__qualname__
	span.set_attribute(_telemetry.ERROR_TYPE, error)
	span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"[:500]))
	return error


async def _complete(
	transport: Any,  # noqa: ANN401
	messages: Sequence[Any],
	tools: Sequence[Any] | None,
	kwargs: dict,
	attributes: dict | None = None,
) -> Reply:
	"""One call to a transport outside a fleet, as the agent loop makes one,
	as a GenAI ``chat`` span with its usage; see the module's docstring."""
	with _chat_span(transport, attributes) as span:
		try:
			reply = await transport.complete(messages, tools, **kwargs)
		except BaseException as exc:
			_failed(span, exc)
			raise
		_telemetry.record_reply(span, reply)
		return reply


class _FirstCalls:
	"""What servers count for agents' first calls: a fixed part, such as tool
	schemas and a template that the messages do not show, plus tokens per
	character. An exponentially weighted least-squares fit of the prompt's
	tokens on its characters; until there are ``1 / alpha`` of them, the calls
	weigh equally.

	The tokens per character are never below ``floor``, the ratio learned
	from the characters later calls add, which carry no fixed part: a first
	prompt counts at least as much as its text. The fit's slope falls below
	it when the first calls cannot tell the two parts apart: while their
	lengths barely vary, or when shorter prompts carry tool schemas and
	longer ones do not. The ratio is then the floor, and the fixed part is the
	rest."""

	__slots__ = ("alpha", "n", "x", "y", "xx", "xy")

	MIN_RATIO = 0.05
	MAX_RATIO = 2.0

	def __init__(self, alpha: float) -> None:
		self.alpha = alpha
		self.n = 0
		self.x = self.y = self.xx = self.xy = 0.0

	def add(self, chars: int, tokens: int) -> None:
		self.n += 1
		w = max(self.alpha, 1 / self.n)
		self.x += w * (chars - self.x)
		self.y += w * (tokens - self.y)
		self.xx += w * (chars * chars - self.xx)
		self.xy += w * (chars * tokens - self.xy)

	def line(self, floor: float) -> tuple[float, float]:
		"""The fixed part, in tokens, and the tokens per character, at least
		``floor``."""
		ratio = floor
		var = self.xx - self.x * self.x
		if var > max((0.1 * self.x) ** 2, 100.0):
			ratio = max((self.xy - self.x * self.y) / var, floor)
		# Not ``max``: a NaN falls to the bottom of the range too.
		ratio = (
			min(ratio, self.MAX_RATIO) if ratio >= self.MIN_RATIO else self.MIN_RATIO
		)
		return max(self.y - ratio * self.x, 0.0), ratio


class _Agent:
	__slots__ = ("chars", "tokens", "last_end", "prev_prompt")

	def __init__(self) -> None:
		self.chars = -1
		self.tokens = 0
		self.last_end: float | None = None
		self.prev_prompt: int | None = None


class _Core:
	"""What every view of one fleet shares."""

	def __init__(
		self,
		replicas: list,
		admission: Admission | None,
		routing: Routing,
		share: float | None,
		process: tuple[int, int] | None,
		pack: bool,
		reply_reserve: int,
		recovery: Recovery | None,
		tracked_agents: int,
		name: str,
	) -> None:
		self.name = name
		self.replicas = [_as_transport(r) for r in replicas]
		self.admission = admission
		self.counts_tokens = bool(admission is not None and admission.counts_tokens)
		self.process = process
		shares: dict[int, float]
		if process is None:
			shares = dict.fromkeys(range(len(replicas)), share)
		else:
			units = getattr(admission, "requests", None)
			if not isinstance(units, int) or units < 1:
				units = process[1]  # one unit of each replica per process
			shares = _parts(*process, len(replicas), units, pack)
		self.gates: list[Gate] = []
		warn_oversize = _OversizeWarning(name)
		for i in range(len(replicas)):
			if i not in shares:
				self.gates.append(_Unused(self.counts_tokens))
				continue
			gate = admission.gate(shares[i]) if admission is not None else _Open()
			if not isinstance(gate, Gate):
				raise TypeError(
					f"{type(admission).__name__}.gate() made a "
					f"{type(gate).__name__}, which is not a Gate: a gate needs "
					"acquire, release, agent_done, load and stats"
				)
			if isinstance(gate, AdmissionGate):
				gate._warn_oversize = warn_oversize
			self.gates.append(gate)
		# The replicas this process sends to, which the routing chooses among,
		# and its parts of them, where they differ.
		self.used = sorted(shares)
		self.routed = [self.gates[i] for i in self.used]
		self.weights: tuple[int, ...] | None = None
		self.cum: tuple[int, ...] | None = None
		if process is not None:
			weights = tuple(shares[i].stop - shares[i].start for i in self.used)
			if len(set(weights)) > 1:
				self.weights, self.cum = weights, tuple(accumulate(weights))
		self.routing = routing
		self.share = share
		self.reply_reserve = reply_reserve
		self.recovery = recovery
		self.tracked_agents = tracked_agents
		self.agents: OrderedDict[str, _Agent] = OrderedDict()
		self.first_ratio = 0.45  # tokens per character, before any first call
		self.delta_ratio = 0.3
		self.alpha = 0.05
		self.first_calls = _FirstCalls(self.alpha)
		self.listeners: list[Callable[[CallRecord, Reply], None]] = []
		self.calls = [0] * len(replicas)
		self.prompt_tokens = 0
		self.completion_tokens = 0
		self.cached_tokens = 0
		self.cache_reported = 0
		self.reasoning_tokens = 0
		self.reasoning_reported = 0
		self.in_flight = 0
		self.in_flight_max = 0
		self.replies_tool = 0
		self.replies_final = 0
		self.invalid_tool_calls = 0
		self.errors: Counter[str] = Counter()
		self.retried = 0
		# Each replica's [prompt, completion] tokens, in the process's totals,
		# which the telemetry reads when the SDK collects; none for a replica
		# this process sends nothing to.
		self.tokens: list[list[int] | None] = [None] * len(replicas)
		for i in self.used:
			self.tokens[i] = _tokens.setdefault((name, i), [0, 0])

	def route(self, agent_id: str) -> int:
		"""The replica for an agent's call: the routing's choice among those
		this process sends to."""
		loads = _Loads(self.routed, self.weights, self.cum)
		return self.used[self.routing.choose(agent_id, loads)]

	def agent(self, agent_id: str) -> _Agent:
		state = self.agents.get(agent_id)
		if state is None:
			state = self.agents[agent_id] = _Agent()
			if len(self.agents) > self.tracked_agents:
				self.agents.popitem(last=False)
		else:
			self.agents.move_to_end(agent_id)
		return state

	def first_call_line(self) -> tuple[float | None, float]:
		"""The fixed part and tokens per character of a first call; no fixed
		part before any first call was counted."""
		if not self.first_calls.n:
			return None, self.first_ratio
		return self.first_calls.line(self.delta_ratio)

	def estimate(self, state: _Agent, chars: int) -> int:
		if state.chars < 0:
			base, ratio = self.first_call_line()
			return int((base or 0.0) + chars * ratio)
		return state.tokens + int(max(chars - state.chars, 0) * self.delta_ratio)

	def learn(
		self, state: _Agent, chars: int, reply_chars: int, prompt: int, completion: int
	) -> None:
		if prompt <= 0:
			return
		a = self.alpha
		if state.chars < 0:
			if chars > 0:
				self.first_calls.add(chars, prompt)
		elif chars - state.chars > 50:
			added = max(prompt - state.tokens, 0) / (chars - state.chars)
			self.delta_ratio += a * (added - self.delta_ratio)
		state.chars = chars + reply_chars
		state.tokens = prompt + completion


_all_fleets: weakref.WeakSet[_Core] = weakref.WeakSet()
_tokens: dict[tuple[str, int], list[int]] = {}
"""The process's fleets' [prompt, completion] tokens, by fleet name and replica:
fleets of one name add up, and a fleet that is gone keeps its count."""


def _process(process: object) -> tuple[int, int]:
	"""``process`` as ``(index, processes)``, checked."""
	try:
		index, processes = (operator.index(x) for x in process)  # type: ignore[attr-defined]
	except (TypeError, ValueError):
		index = processes = -1
	if not 0 <= index < processes:
		raise ValueError(
			f"process must be (index, processes), two integers with 0 <= index "
			f"< processes, not {process!r}"
		)
	return index, processes


class Fleet:
	"""See the module's docstring.

	``share`` is this process's share of every replica's caps, rounded down,
	as in 0.6.0. ``process=(index, processes)`` says instead which of the
	agent processes that use the replicas this one is: the processes then
	split each replica's caps exactly. ``pack=True``, with ``process``, gives
	each process its part from as few replicas as hold it, rather than from
	every replica."""

	def __init__(
		self,
		replicas: Sequence[Any],
		*,
		admission: Admission | None = None,
		routing: Routing | None = None,
		share: float | None = None,
		process: tuple[int, int] | None = None,
		pack: bool = False,
		reply_reserve: int = 64,
		recovery: Recovery | None = None,
		tracked_agents: int = 50_000,
		name: str = "",
	) -> None:
		if not replicas:
			raise ValueError("a fleet needs at least one replica")
		if share is not None and process is not None:
			raise ValueError(
				"give share or process, not both: with process, the processes "
				"split each replica's caps exactly"
			)
		if process is not None:
			process = _process(process)
		elif pack:
			raise ValueError("pack needs process=(index, processes)")
		elif share is None:
			share = 1.0
		if share is not None and not 0 < share <= 1:
			raise ValueError("share must be in (0, 1]")
		if reply_reserve < 0:
			raise ValueError("reply_reserve must not be negative")
		self._core = _Core(
			list(replicas),
			admission,
			routing or AgentAffinity(),
			share,
			process,
			pack,
			reply_reserve,
			recovery,
			tracked_agents,
			name,
		)
		_all_fleets.add(self._core)

	@property
	def replicas(self) -> int:
		"""How many replicas the fleet has."""
		return len(self._core.replicas)

	@property
	def transports(self) -> list[Any]:
		"""Each replica's transport now: ``Recovery`` may have replaced one."""
		return list(self._core.replicas)

	def add_listener(self, listener: Callable[[CallRecord, Reply], None]) -> None:
		"""Call ``listener(record, reply)`` after each call."""
		self._core.listeners.append(listener)

	def load(self) -> list[ReplicaLoad]:
		"""Each replica's load in this process."""
		return [g.load() for g in self._core.gates]

	def agent_done(self, agent_id: str) -> None:
		"""Say that an agent ended: every gate hears it, and the fleet forgets
		the agent's size. Call it once the agent makes no more calls, for
		example from the agent host's ``on_done``."""
		core = self._core
		core.agents.pop(agent_id, None)
		for gate in core.gates:
			gate.agent_done(agent_id)

	async def ainvoke(
		self,
		agent_id: str,
		messages: Sequence[Any],
		*,
		tools: Sequence[Any] | None = None,
		reserve: int = 0,
		meta: Any = None,  # noqa: ANN401
		**kwargs: Any,
	) -> Reply:
		"""Send an agent's call to its replica, through admission; return the
		replica's ``Reply``.

		``messages`` are OpenAI-format dicts (a LangChain transport also takes
		LangChain messages). ``tools`` are the tools' JSON schemas (a LangChain
		transport also takes LangChain tools). ``kwargs`` go to the transport's
		``complete`` for this call only. ``reserve`` adds tokens to the call's
		admission estimate, for a reply longer than usual, such as one that may
		think. ``meta`` goes into the call's ``CallRecord``.
		"""
		if reserve < 0:
			raise ValueError("reserve must not be negative")
		core = self._core
		i = core.route(agent_id)
		gate = core.gates[i]
		state = core.agent(agent_id)
		chars = _message_chars(messages)
		estimate = core.estimate(state, chars) + core.reply_reserve + reserve
		weight = estimate if core.counts_tokens else 0
		after_tool = _after_tool(messages)
		asked_at = time.time()
		asked = time.monotonic()
		attrs = {_telemetry.FLEET_NAME: core.name, _telemetry.FLEET_REPLICA: i}
		transport = core.replicas[i]
		span_attrs = {**attrs, _telemetry.GEN_AI_CONVERSATION_ID: agent_id}
		if state.last_end is not None:
			span_attrs[_telemetry.FLEET_GAP] = asked_at - state.last_end
		if after_tool is not None:
			span_attrs[_telemetry.FLEET_AFTER_TOOL] = after_tool
		sent = list(messages)
		failing_since: float | None = None
		wait_s = 0.0  # for admission, over every try
		resends = 0
		resend: str | None = None  # the error a resend is waiting to be admitted for
		got = asked
		error: str | None = None
		recovery_s = 0.0
		# One chat span per call, from routing to the reply: the wait for
		# admission, every try and any outage waited out.
		with _chat_span(transport, span_attrs) as span:
			try:
				with _agents.activity(_agents.AgentState.MODEL_QUEUED):
					while True:
						queued = time.monotonic()
						try:
							await gate.acquire(weight, agent_id)
						except BaseException:
							wait_s += time.monotonic() - queued  # cut short
							raise
						failed: BaseException | None = None
						try:
							got = time.monotonic()
							wait_s += got - queued
							_telemetry.ADMISSION_WAIT.record(got - queued, attrs)
							if resend is not None:  # sent again: count it now
								resends += 1
								core.retried += 1
								_telemetry.RETRIES.add(
									1, {**attrs, _telemetry.ERROR_TYPE: resend}
								)
								resend = None
							core.in_flight += 1
							core.in_flight_max = max(core.in_flight_max, core.in_flight)
							try:
								with _agents.activity(_agents.AgentState.MODEL_REQUEST):
									reply = await core.replicas[i].complete(
										sent, tools, **kwargs
									)
							except Exception as exc:
								core.errors[type(exc).__name__] += 1
								_telemetry.MODEL_SERVICE.record(
									time.monotonic() - got,
									{
										**attrs,
										_telemetry.ERROR_TYPE: type(exc).__qualname__,
									},
								)
								if core.recovery is None or not _transient(exc):
									raise
								failed = exc
							except BaseException as exc:
								# Cancelled at the server: the request was made.
								_telemetry.MODEL_SERVICE.record(
									time.monotonic() - got,
									{
										**attrs,
										_telemetry.ERROR_TYPE: type(exc).__qualname__,
									},
								)
								raise
							finally:
								core.in_flight -= 1
							done = time.monotonic()
						finally:
							gate.release(weight, agent_id)
						if failed is None:
							break
						# outage_s bounds the call, not only one watch: a call that
						# fails again once its replica is back pauses before the
						# next try, and fails outage_s after its first failure.
						recovery = core.recovery
						if failing_since is None:
							failing_since = time.monotonic()
						else:
							await asyncio.sleep(recovery.poll_s)
						left = recovery.outage_s - (time.monotonic() - failing_since)
						model = (
							await recovery._wait(i, left, attrs) if left > 0 else None
						)
						if model is None:
							raise failed
						if not _same_replica(model, core.replicas[i]):
							old, core.replicas[i] = (
								core.replicas[i],
								_as_transport(model),
							)
							_close_later(old)
						resend = type(failed).__qualname__
			except BaseException as exc:
				error = _failed(span, exc)
				raise
			finally:
				ended = time.monotonic()
				if failing_since is not None:
					# The tries that failed and the wait for the replica: for a
					# call that failed, all but its waits for admission.
					last = got if error is None else ended
					recovery_s = max(last - asked - wait_s, 0.0)
				if span.is_recording():
					span.set_attribute(_telemetry.FLEET_ADMISSION_WAIT, wait_s)
					if resends:
						span.set_attribute(_telemetry.FLEET_RESEND_COUNT, resends)
					if failing_since is not None:
						span.set_attribute(_telemetry.FLEET_RECOVERY_WAIT, recovery_s)
					if core.replicas[i] is not transport:  # Recovery replaced it
						_renamed(span, transport, core.replicas[i])
				_telemetry.CALL_DURATION.record(
					ended - asked,
					attrs if error is None else {**attrs, _telemetry.ERROR_TYPE: error},
				)
			_telemetry.record_reply(span, reply)
		_telemetry.MODEL_SERVICE.record(done - got, attrs)
		return self._account(
			core,
			i,
			agent_id,
			state,
			chars,
			reply,
			asked_at,
			wait_s,
			done - got,
			recovery_s,
			estimate,
			after_tool,
			meta,
		)

	def _account(
		self,
		core: _Core,
		i: int,
		agent_id: str,
		state: _Agent,
		chars: int,
		reply: Reply,
		asked_at: float,
		wait_s: float,
		service_s: float,
		recovery_s: float,
		estimate: int,
		after_tool: str | None,
		meta: Any,
	) -> Reply:
		t_end = time.time()
		# when the agent committed to the reply's tool calls
		_agents.note_reply(agent_id, t_end, [c.id for c in reply.tool_calls if c.id])
		core.calls[i] += 1
		usage = reply.usage or Usage()
		prompt = usage.prompt_tokens or 0
		completion = usage.completion_tokens or 0
		cached = usage.cached_tokens
		if cached is not None:
			core.cache_reported += 1
			core.cached_tokens += int(cached)
		reasoning = usage.reasoning_tokens
		if reasoning is not None:
			reasoning = int(reasoning)
			core.reasoning_reported += 1
			core.reasoning_tokens += reasoning
		core.prompt_tokens += prompt
		core.completion_tokens += completion
		tokens = core.tokens[i]
		tokens[0] += prompt
		tokens[1] += completion
		record = CallRecord(
			agent_id=agent_id,
			replica=i,
			t_end=t_end,
			wait_s=wait_s,
			service_s=service_s,
			prompt_tokens=prompt,
			cached_tokens=None if cached is None else int(cached),
			completion_tokens=completion,
			estimate=estimate,
			gap_s=None if state.last_end is None else asked_at - state.last_end,
			after_tool=after_tool,
			prev_prompt_tokens=state.prev_prompt,
			reasoning_tokens=reasoning,
			meta=meta,
			recovery_s=recovery_s,
		)
		# The conversation keeps the reply but not its thinking.
		kept = completion - (reasoning or 0)
		core.learn(state, chars, _reply_chars(reply), prompt, kept)
		state.last_end = t_end
		state.prev_prompt = prompt
		if reply.tool_calls:
			core.replies_tool += 1
		else:
			core.replies_final += 1
		if reply.invalid_tool_calls:
			core.invalid_tool_calls += 1
		for listener in core.listeners:
			listener(record, reply)
		return reply

	def chat_model(self) -> Any:  # noqa: ANN401  # a FleetChatModel
		"""This fleet as a LangChain chat model, for LangGraph graphs and
		prebuilt agents: ``flowgentic.langchain.FleetChatModel``. Needs the
		``langgraph`` extra."""
		from flowgentic.langchain import FleetChatModel

		return FleetChatModel(fleet=self)

	def stats(self) -> dict:
		"""The fleet's counters, with its gates' and its recovery's."""
		core = self._core
		base, ratio = core.first_call_line()
		return {
			"name": core.name,
			"replicas": len(core.replicas),
			"share": core.share,
			"process": None if core.process is None else list(core.process),
			"replicas_used": list(core.used),
			"reply_reserve_tokens": core.reply_reserve,
			"calls": sum(core.calls),
			"calls_per_replica": list(core.calls),
			"prompt_tokens": core.prompt_tokens,
			"completion_tokens": core.completion_tokens,
			"cached_tokens": core.cached_tokens if core.cache_reported else None,
			"calls_with_cache_count": core.cache_reported,
			"reasoning_tokens": (
				core.reasoning_tokens if core.reasoning_reported else None
			),
			"calls_with_reasoning_count": core.reasoning_reported,
			"in_flight_max": core.in_flight_max,
			"gates": [g.stats() for g in core.gates],
			"tokens_per_char": {
				"first_call": round(ratio, 4),
				"first_call_base": None if base is None else round(base),
				"added": round(core.delta_ratio, 4),
			},
			"replies_tool_call": core.replies_tool,
			"replies_final": core.replies_final,
			"invalid_tool_calls": core.invalid_tool_calls,
			"errors": dict(core.errors),
			"retried_calls": core.retried,
			"recovery": None if core.recovery is None else core.recovery.stats(),
		}


def _loads(kind: str) -> dict[tuple[str, int], int]:
	"""One kind of load, ``tokens``, ``requests`` or ``waiting``, per fleet name
	and replica, summed over the process's fleets; read by the telemetry. A
	replica that a fleet sends nothing to has no load of that fleet's."""
	out: dict[tuple[str, int], int] = {}
	for core in list(_all_fleets):
		for i, gate in zip(core.used, core.routed):
			key = (core.name, i)
			out[key] = out.get(key, 0) + getattr(gate.load(), kind)
	return out


class _NeedsExtra(AttributeError):
	"""A name of another extra's: an ``AttributeError``, so that ``hasattr``
	says it is not there and ``import *`` skips it, with the install hint."""


def __getattr__(name: str) -> Any:  # noqa: ANN401
	# FleetChatModel lives with LangChain's adapter; this name stays for the
	# code that imports it from here. It is not in __all__: import * works
	# without LangChain.
	if name == "FleetChatModel":
		try:
			from flowgentic.langchain import FleetChatModel
		except ModuleNotFoundError as exc:
			raise _NeedsExtra(str(exc), name=name, obj=__name__) from exc
		return FleetChatModel
	raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
