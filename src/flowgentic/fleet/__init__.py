"""The model fleet's client: one LangChain chat model per model server replica.

Agents' model calls go to placed models, one replica per GPU or server. The
fleet decides, for each call, which replica takes it and when:

- **Routing.** ``AgentAffinity`` sends each agent to one replica by a stable
  hash of its id, the same in every process, so the agent's growing prompt
  stays in that replica's prefix cache. ``RoundRobin`` and ``LeastLoaded`` are
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
  replica's caps. This holds because every process routes by the same hash.
  It is a static split.
- **Recovery.** With ``Recovery``, a call whose server went away (a connection
  error, a timeout, a 5xx, a 401 after a new key) releases its place, waits for
  the replica to come back, and is sent again; ``outage_s`` after its first
  failure it fails, even if the replica came back and the call kept failing.
  A check that raises or hangs counts as the replica still being down.
- **Accounting.** One ``CallRecord`` per call goes to the listeners: the wait
  for admission, the time at the server, the tokens, the prompt tokens the
  server found in its prefix cache and the reply's reasoning tokens when the
  server reports them, the gap since the agent's previous call, the tool whose
  result came just before, and the caller's ``meta``. The fleet itself keeps
  counters.
- **Metrics.** The wait for admission and the time at the replica are
  OpenTelemetry histograms; each replica's tokens and requests in flight, and
  its requests waiting, are gauges. They carry the fleet's ``name``, so the
  fleets of several models in one process stay apart, and the replica's index.

A request's live context is estimated before it is sent: the agent's size
after its previous call, as the server counted it, plus the characters added
since, at the tokens per character the server's counts give, plus a reserve
for the reply and the call's own ``reserve``. The size leaves out the previous
reply's reasoning tokens, when the server reports them
(``output_token_details.reasoning``): the conversation does not keep a model's
thinking. An agent's first call is estimated from the sizes the server counted
for earlier first calls, as a fixed part, such as tool schemas and a template
that the messages do not show, plus tokens per character.

``fleet.ainvoke(agent_id, messages)`` is the direct path, for a graph's own
model node. ``fleet.chat_model()`` is a LangChain chat model for prebuilt
agents, which reads the agent's id from the run config's ``thread_id``: the
config passed to it, or else the LangGraph run it is called in. It opens no
LangChain run of its own: it hands the caller's config to the replica, whose
run is the call's only one. Both take, per call, settings for the replica
(``extra_body``, ``max_tokens``, ...), which reach the replica for that call
only, a ``reserve`` of tokens beyond the usual reply, such as a thinking
budget, and a ``meta`` that the call's record carries. Which settings make a
model think is the model's business, not the fleet's.
"""

from __future__ import annotations

import asyncio
import contextvars
import itertools
import json
import logging
import time
import weakref
import zlib
from collections import Counter, OrderedDict, deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

try:
	from langchain_core.language_models import BaseChatModel, LanguageModelInput
	from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
	from langchain_core.outputs import ChatResult
	from langchain_core.runnables import RunnableConfig
	from langchain_core.runnables.config import ensure_config
except ModuleNotFoundError as exc:
	if exc.name != "langchain_core":
		raise
	raise ModuleNotFoundError(
		"flowgentic.fleet needs LangChain: install flowgentic[langgraph]",
		name=exc.name,
	) from exc

from flowgentic import _agents, _telemetry

log = logging.getLogger(__name__)

__all__ = [
	"Admission",
	"AdmissionGate",
	"AgentAffinity",
	"CallRecord",
	"Fleet",
	"FleetChatModel",
	"Gate",
	"LeastLoaded",
	"Recovery",
	"ReplicaLoad",
	"RequestAdmission",
	"RoundRobin",
	"Routing",
	"TokenAdmission",
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


class AdmissionGate:
	"""Admits requests to one replica while the tokens of live context and the
	requests in flight stay under their caps. ``tokens=None`` counts requests
	only. First come, first served, whatever the agent; a request larger than
	the token cap is admitted alone.

	A gate holds one process's share of a replica's caps, and cannot see the
	other processes' requests. A request admitted alone above the share's token
	cap may therefore take the replica past its cap, if other processes do the
	same: such requests are counted (``oversize`` in ``stats()``), and the
	first is logged as a warning. A ``Gate``."""

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
			if self.oversize == 1:
				log.warning(
					"a model request of %d tokens is larger than this process's "
					"token cap at its replica, %d, so it runs alone; requests like "
					"it from several processes can take the replica past its cap. "
					"Raise the cap or this process's share so that the largest "
					"context fits in one share",
					n,
					self.cap_tokens,
				)
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


class Admission(Protocol):
	"""Makes one gate per replica, for a process's ``share`` of its caps.
	``counts_tokens`` says whether its gates are given each call's estimated
	tokens, or 0."""

	counts_tokens: bool

	def gate(self, share: float) -> Gate: ...


def _share(cap: int, share: float) -> int:
	# A cap below 1 is a mistake, not a cap of 1: refuse it before the share
	# is rounded up.
	if cap < 1:
		raise ValueError("the admission caps must be at least 1")
	return max(1, int(cap * share))


@dataclass(frozen=True, slots=True)
class TokenAdmission:
	"""Caps per replica, across all agent processes: tokens of live context in
	flight, and requests in flight."""

	tokens: int
	requests: int
	counts_tokens = True

	def gate(self, share: float) -> AdmissionGate:
		return AdmissionGate(_share(self.tokens, share), _share(self.requests, share))


@dataclass(frozen=True, slots=True)
class RequestAdmission:
	"""A cap per replica, across all agent processes, on requests in flight."""

	requests: int
	counts_tokens = False

	def gate(self, share: float) -> AdmissionGate:
		return AdmissionGate(None, _share(self.requests, share))


# --- routing -----------------------------------------------------------------------


class Routing(Protocol):
	"""Chooses the replica for an agent's call."""

	def choose(self, agent_id: str, loads: Sequence[ReplicaLoad]) -> int: ...


class AgentAffinity:
	"""Each agent always to the same replica, by a stable hash of its id."""

	def choose(self, agent_id: str, loads: Sequence[ReplicaLoad]) -> int:
		return _stable_hash(agent_id) % len(loads)


class RoundRobin:
	"""Calls to the replicas in turn, whatever the agent."""

	def __init__(self) -> None:
		self._next = itertools.count()

	def choose(self, agent_id: str, loads: Sequence[ReplicaLoad]) -> int:
		return next(self._next) % len(loads)


class LeastLoaded:
	"""Each call to the replica with the fewest requests in flight and waiting."""

	def choose(self, agent_id: str, loads: Sequence[ReplicaLoad]) -> int:
		return min(
			range(len(loads)),
			key=lambda i: (loads[i].requests + loads[i].waiting, loads[i].tokens),
		)


# --- recovery ----------------------------------------------------------------------

_TRANSIENT = frozenset(
	{
		"APIConnectionError",
		"APITimeoutError",
		"InternalServerError",
		"AuthenticationError",
	}
)
"""Errors of a server that is down, restarting or overloaded, or that came back
with a new key, by the OpenAI client's class names. Matched along the class's
MRO, so langchain-openai's own wrappers of them match too."""


def _transient(exc: BaseException) -> bool:
	"""Whether ``exc`` is a server's that is down, restarting or overloaded."""
	return any(c.__name__ in _TRANSIENT for c in type(exc).__mro__)


class Recovery:
	"""Waits out a replica whose server went away, for at most ``outage_s``.

	``check(i)``, an ``async def``, returns replica ``i``'s chat model once its
	server serves again, a new one if its endpoint or key changed, or ``None``
	while it is down. The first call that fails on a replica starts one watch,
	which calls ``check`` every ``poll_s`` seconds until the replica serves or
	``outage_s`` has passed; later calls wait on the same watch. A check that
	raises, or does not answer within ``check_timeout_s``, counts as the replica
	still being down. The watch serves every call on the replica, so it runs in
	a context of its own, not in the first failing call's. A call that fails
	again after its replica came back waits ``poll_s`` before it is sent again,
	and fails ``outage_s`` after its first failure.
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
		"""Replica ``i``'s model once it serves again, or ``None`` after
		``outage_s``, or after ``timeout`` seconds for this caller."""
		future = self._watches.get(i)
		if future is None:
			future = self._watches[i] = asyncio.get_running_loop().create_future()
			# No agent, span or run config of the call that happened to fail first.
			task = asyncio.create_task(
				self._watch(i, future), context=contextvars.Context()
			)
			self._tasks.add(task)
			task.add_done_callback(self._tasks.discard)
		# asyncio.wait leaves the watch running when this caller times out or
		# is cancelled.
		done, _ = await asyncio.wait({future}, timeout=timeout)
		return future.result() if done else None

	async def _watch(self, i: int, future: asyncio.Future) -> None:
		t0 = time.monotonic()
		failures = 0
		try:
			while True:
				left = self.outage_s - (time.monotonic() - t0)
				if left <= 0:
					self.gave_up[i] += 1
					self.down_s[i] += time.monotonic() - t0
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
					self.outages[i] += 1
					self.down_s[i] += time.monotonic() - t0
					future.set_result(model)
					return
				await asyncio.sleep(self.poll_s)
		finally:
			if not future.done():  # the watch was cancelled: its calls fail as they are
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
	"""One model call."""

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


def _message_chars(messages: Sequence[BaseMessage]) -> int:
	"""Characters of a conversation: contents and tool calls."""
	n = 0
	for m in messages:
		c = m.content
		n += len(c) if isinstance(c, str) else len(str(c))
		for call in getattr(m, "tool_calls", None) or ():
			n += len(call.get("name", "")) + len(json.dumps(call.get("args", {})))
	return n


class _FirstCalls:
	"""What servers count for agents' first calls: a fixed part, such as tool
	schemas and a template that the messages do not show, plus tokens per
	character. An exponentially weighted least-squares fit of the prompt's
	tokens on its characters; until there are ``1 / alpha`` of them, the calls
	weigh equally.

	While the first calls' lengths barely vary, they cannot tell the two
	apart: the ratio is then the one learned from the characters later calls
	add, and the fixed part is the rest."""

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

	def line(self, fallback: float) -> tuple[float, float]:
		"""The fixed part, in tokens, and the tokens per character."""
		var = self.xx - self.x * self.x
		if var > max((0.1 * self.x) ** 2, 100.0):
			ratio = (self.xy - self.x * self.y) / var
		else:
			ratio = fallback
		ratio = min(max(ratio, self.MIN_RATIO), self.MAX_RATIO)
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
		share: float,
		reply_reserve: int,
		recovery: Recovery | None,
		tracked_agents: int,
		name: str,
	) -> None:
		self.name = name
		self.replicas = list(replicas)
		self.generation = [0] * len(replicas)
		self.admission = admission
		self.counts_tokens = bool(admission is not None and admission.counts_tokens)
		self.gates: list[Gate] = []
		for _ in replicas:
			gate = admission.gate(share) if admission is not None else _Open()
			if not isinstance(gate, Gate):
				raise TypeError(
					f"{type(admission).__name__}.gate() made a "
					f"{type(gate).__name__}, which is not a Gate: a gate needs "
					"acquire, release, agent_done, load and stats"
				)
			self.gates.append(gate)
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
		self.listeners: list[Callable[[CallRecord, AIMessage], None]] = []
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


class Fleet:
	"""See the module's docstring."""

	def __init__(
		self,
		replicas: Sequence[Any],
		*,
		admission: Admission | None = None,
		routing: Routing | None = None,
		share: float = 1.0,
		reply_reserve: int = 64,
		recovery: Recovery | None = None,
		tracked_agents: int = 50_000,
		name: str = "",
	) -> None:
		if not replicas:
			raise ValueError("a fleet needs at least one replica")
		if not 0 < share <= 1:
			raise ValueError("share must be in (0, 1]")
		self._core = _Core(
			list(replicas),
			admission,
			routing or AgentAffinity(),
			share,
			reply_reserve,
			recovery,
			tracked_agents,
			name,
		)
		_all_fleets.add(self._core)
		self._tools: tuple[Any, dict] | None = None
		self._bound: dict[int, tuple[int, Any]] = {}

	@property
	def replicas(self) -> int:
		"""How many replicas the fleet has."""
		return len(self._core.replicas)

	def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Fleet:
		"""A view of this fleet whose replicas are bound to ``tools``; it shares
		the gates, the estimates and the counters."""
		view = Fleet.__new__(Fleet)
		view._core = self._core
		view._tools = (list(tools), kwargs)
		view._bound = {}
		return view

	def _model(self, i: int) -> Any:
		core = self._core
		cached = self._bound.get(i)
		if cached is not None and cached[0] == core.generation[i]:
			return cached[1]
		model = core.replicas[i]
		if self._tools is not None:
			model = model.bind_tools(self._tools[0], **self._tools[1])
		self._bound[i] = (core.generation[i], model)
		return model

	def add_listener(self, listener: Callable[[CallRecord, AIMessage], None]) -> None:
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
		messages: Sequence[BaseMessage],
		*,
		reserve: int = 0,
		meta: Any = None,
		**kwargs: Any,
	) -> AIMessage:
		"""Send an agent's call to its replica, through admission.

		``kwargs`` go to the replica's ``ainvoke`` for this call only, a
		``config`` included. ``reserve`` adds tokens to the call's admission
		estimate, for a reply longer than usual, such as one that may think.
		``meta`` goes into the call's ``CallRecord``.
		"""
		if reserve < 0:
			raise ValueError("reserve must not be negative")
		core = self._core
		i = core.routing.choose(agent_id, [g.load() for g in core.gates])
		gate = core.gates[i]
		state = core.agent(agent_id)
		chars = _message_chars(messages)
		estimate = core.estimate(state, chars) + core.reply_reserve + reserve
		weight = estimate if core.counts_tokens else 0
		last = messages[-1] if messages else None
		after_tool = last.name if isinstance(last, ToolMessage) else None
		asked_at = time.time()
		asked = time.monotonic()
		attrs = {_telemetry.FLEET_NAME: core.name, _telemetry.FLEET_REPLICA: i}
		failing_since: float | None = None
		with _agents.activity(_agents.AgentState.MODEL_QUEUED):
			while True:
				await gate.acquire(weight, agent_id)
				failed: BaseException | None = None
				try:
					got = time.monotonic()
					core.in_flight += 1
					core.in_flight_max = max(core.in_flight_max, core.in_flight)
					try:
						with _agents.activity(_agents.AgentState.MODEL_REQUEST):
							reply = await self._model(i).ainvoke(
								list(messages), **kwargs
							)
					except Exception as exc:
						core.errors[type(exc).__name__] += 1
						if core.recovery is None or not _transient(exc):
							raise
						failed = exc
					finally:
						core.in_flight -= 1
					done = time.monotonic()
				finally:
					gate.release(weight, agent_id)
				if failed is None:
					break
				# outage_s bounds the call, not only one watch: a call that fails
				# again once its replica is back pauses before the next try, and
				# fails outage_s after its first failure.
				recovery = core.recovery
				if failing_since is None:
					failing_since = time.monotonic()
				else:
					await asyncio.sleep(recovery.poll_s)
				left = recovery.outage_s - (time.monotonic() - failing_since)
				model = await recovery.wait(i, left) if left > 0 else None
				if model is None:
					raise failed
				if model is not core.replicas[i]:
					core.replicas[i] = model
					core.generation[i] += 1
				core.retried += 1
		_telemetry.ADMISSION_WAIT.record(got - asked, attrs)
		_telemetry.MODEL_SERVICE.record(done - got, attrs)
		return self._account(
			core,
			i,
			agent_id,
			state,
			chars,
			reply,
			asked_at,
			asked,
			got,
			done,
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
		reply: AIMessage,
		asked_at: float,
		asked: float,
		got: float,
		done: float,
		estimate: int,
		after_tool: str | None,
		meta: Any,
	) -> AIMessage:
		t_end = time.time()
		core.calls[i] += 1
		usage = getattr(reply, "usage_metadata", None) or {}
		prompt = usage.get("input_tokens", 0) or 0
		completion = usage.get("output_tokens", 0) or 0
		cached = (usage.get("input_token_details") or {}).get("cache_read")
		if cached is not None:
			core.cache_reported += 1
			core.cached_tokens += int(cached)
		reasoning = (usage.get("output_token_details") or {}).get("reasoning")
		if reasoning is not None:
			reasoning = int(reasoning)
			core.reasoning_reported += 1
			core.reasoning_tokens += reasoning
		core.prompt_tokens += prompt
		core.completion_tokens += completion
		record = CallRecord(
			agent_id=agent_id,
			replica=i,
			t_end=t_end,
			wait_s=got - asked,
			service_s=done - got,
			prompt_tokens=prompt,
			cached_tokens=None if cached is None else int(cached),
			completion_tokens=completion,
			estimate=estimate,
			gap_s=None if state.last_end is None else asked_at - state.last_end,
			after_tool=after_tool,
			prev_prompt_tokens=state.prev_prompt,
			reasoning_tokens=reasoning,
			meta=meta,
		)
		# The conversation keeps the reply but not its thinking.
		kept = completion - (reasoning or 0)
		core.learn(state, chars, _message_chars([reply]), prompt, kept)
		state.last_end = t_end
		state.prev_prompt = prompt
		if reply.tool_calls:
			core.replies_tool += 1
		else:
			core.replies_final += 1
		if getattr(reply, "invalid_tool_calls", None):
			core.invalid_tool_calls += 1
		for listener in core.listeners:
			listener(record, reply)
		return reply

	def chat_model(self) -> FleetChatModel:
		"""This fleet as a LangChain chat model; see the module's docstring."""
		return FleetChatModel(fleet=self)

	def stats(self) -> dict:
		"""The fleet's counters, with its gates' and its recovery's."""
		core = self._core
		base, ratio = core.first_call_line()
		return {
			"name": core.name,
			"replicas": len(core.replicas),
			"share": core.share,
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


class FleetChatModel(BaseChatModel):
	"""A fleet as a LangChain chat model.

	The agent is the run config's ``thread_id``, as LangGraph sets it for each
	thread: from the config passed to ``ainvoke``, or else from the LangGraph
	run the model is called in; without one, the agent host's current agent.
	The model opens no LangChain run of its own. It hands the caller's config
	to the replica, so callbacks, tracers and LangGraph's streaming see one
	model call, the replica's. ``reserve`` and ``meta`` are the fleet's; other
	keywords go to the replica, as with ``Fleet.ainvoke``.
	"""

	fleet: Any

	@property
	def _llm_type(self) -> str:
		return "flowgentic-fleet"

	def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> FleetChatModel:
		return FleetChatModel(fleet=self.fleet.bind_tools(tools, **kwargs))

	async def ainvoke(
		self,
		input: LanguageModelInput,
		config: RunnableConfig | None = None,
		*,
		stop: list[str] | None = None,
		**kwargs: Any,
	) -> AIMessage:
		config = ensure_config(config)
		agent = config.get("configurable", {}).get("thread_id")
		agent = agent or _agents.current_agent()
		if not agent:
			raise ValueError(
				"the fleet routes by agent: pass a thread_id in the config, or run "
				"it in a LangGraph thread or an agent host"
			)
		if stop is not None:
			kwargs["stop"] = stop
		messages = self._convert_input(input).to_messages()
		return await self.fleet.ainvoke(str(agent), messages, config=config, **kwargs)

	def _generate(
		self,
		messages: list[BaseMessage],
		stop: Any = None,
		run_manager: Any = None,
		**kwargs: Any,
	) -> ChatResult:
		raise NotImplementedError("the fleet is asynchronous: use ainvoke")


def _loads(kind: str) -> dict[tuple[str, int], int]:
	"""One kind of load, ``tokens``, ``requests`` or ``waiting``, per fleet name
	and replica, summed over the process's fleets; read by the telemetry."""
	out: dict[tuple[str, int], int] = {}
	for core in list(_all_fleets):
		for i, gate in enumerate(core.gates):
			key = (core.name, i)
			out[key] = out.get(key, 0) + getattr(gate.load(), kind)
	return out
