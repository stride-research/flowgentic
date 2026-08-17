"""Tests for FlowGentic tool lifecycle instrumentation.

Verifies that all 6 timestamps (wrap_start, wrap_end, invoke_start,
resolve_end, collect_start, invoke_end) are emitted in correct
chronological order, that derived durations are non-negative,
and that cache_hit tracking works correctly.
"""

import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

import flowgentic.agent_orchestration_frameworks.langgraph as _lg_mod

# Replace _langchain_tool with an identity function so the tests don't
# require langchain-core.  The lifecycle events are what we're testing,
# not the LangChain tool decorator.
_lg_mod._langchain_tool = lambda fn: fn  # type: ignore[attr-defined]

from flowgentic.agent_orchestration_frameworks.langgraph import \
	LanGraphOrchestrator

from flowgentic.backend_engines.base import BaseEngine


# ---------------------------------------------------------------------------
# Fake engine that mimics AsyncFlowEngine's lifecycle instrumentation
# without requiring radical.asyncflow as a dependency.
# ---------------------------------------------------------------------------
class FakeAsyncFlowEngine(BaseEngine):
	"""A lightweight stand-in for AsyncFlowEngine.

	Reproduces the same resolve / collect emit pattern so the full
	lifecycle can be tested end-to-end with the real
	LanGraphOrchestrator.
	"""

	def __init__(
		self,
		observer: Optional[Callable[[Dict[str, Any]], None]] = None,
	):
		super().__init__(observer=observer)
		self._task_registry: Dict[
			Tuple[Callable, Tuple[Tuple[str, Any], ...]], Any
		] = {}

	async def execute_tool(
		self,
		func: Callable,
		*args,
		task_kwargs: Optional[Dict[str, Any]] = None,
		invocation_id: Optional[str] = None,
		**kwargs,
	) -> Dict[str, Any]:
		import time

		task_kwargs = task_kwargs or {}
		key = (func, tuple(sorted(task_kwargs.items())))

		cache_hit = key in self._task_registry
		if not cache_hit:
			# Instead of self.flow.function_task(), store the func directly
			self._task_registry[key] = func

		task = self._task_registry[key]
		task_name = getattr(func, "__name__", str(func))

		# Ts_resolve_end
		self.emit(
			{
				"event": "tool_resolve_end",
				"ts": time.perf_counter(),
				"tool_name": task_name,
				"invocation_id": invocation_id,
				"cache_hit": cache_hit,
			}
		)

		result = await task(*args, **kwargs)

		# Ts_collect_start
		self.emit(
			{
				"event": "tool_collect_start",
				"ts": time.perf_counter(),
				"tool_name": task_name,
				"invocation_id": invocation_id,
			}
		)

		return result

	async def wrap_node(self, node_func: Callable):
		return node_func


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _collect_events() -> Tuple[List[Dict[str, Any]], Callable]:
	"""Return a list and an observer callback that appends to it."""
	events: List[Dict[str, Any]] = []

	def observer(event: Dict[str, Any]) -> None:
		events.append(event)

	return events, observer


def _events_by_name(
	events: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
	"""Index events by their ``event`` name (last occurrence wins)."""
	return {e["event"]: e for e in events}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_full_lifecycle_timestamps():
	"""All 6 lifecycle events are emitted in chronological order."""
	events, observer = _collect_events()
	engine = FakeAsyncFlowEngine(observer=observer)
	orchestrator = LanGraphOrchestrator(engine=engine)

	# A trivial async tool
	async def noop_tool(x: str) -> dict:
		"""A no-op tool for testing."""
		return {"result": x}

	# Phase 1: wrapping
	wrapped = orchestrator.hpc_task(noop_tool)

	# Phase 2: invocation — call the inner coroutine directly
	result = await wrapped.coroutine("hello")

	assert result == {"result": "hello"}

	# Expected event order
	expected_order = [
		"tool_wrap_start",
		"tool_wrap_end",
		"tool_invoke_start",
		"tool_resolve_end",
		"tool_collect_start",
		"tool_invoke_end",
	]

	event_names = [e["event"] for e in events]
	assert event_names == expected_order, (
		f"Event order mismatch.\n  Expected: {expected_order}\n  Got:      {event_names}"
	)

	# All timestamps must be monotonically non-decreasing
	timestamps = [e["ts"] for e in events]
	for i in range(1, len(timestamps)):
		assert timestamps[i] >= timestamps[i - 1], (
			f"Timestamp regression: {events[i - 1]['event']} "
			f"({timestamps[i - 1]}) > {events[i]['event']} ({timestamps[i]})"
		)


@pytest.mark.asyncio
async def test_derived_durations_non_negative():
	"""D_resolve, D_collect, D_overhead, D_total >= 0."""
	events, observer = _collect_events()
	engine = FakeAsyncFlowEngine(observer=observer)
	orchestrator = LanGraphOrchestrator(engine=engine)

	async def noop_tool(x: str) -> dict:
		"""A no-op tool for testing."""
		return {"result": x}

	wrapped = orchestrator.hpc_task(noop_tool)
	await wrapped.coroutine("hello")

	by_name = _events_by_name(events)

	ts_invoke_start = by_name["tool_invoke_start"]["ts"]
	ts_resolve_end = by_name["tool_resolve_end"]["ts"]
	ts_collect_start = by_name["tool_collect_start"]["ts"]
	ts_invoke_end = by_name["tool_invoke_end"]["ts"]

	d_resolve = ts_resolve_end - ts_invoke_start
	d_backend = ts_collect_start - ts_resolve_end
	d_collect = ts_invoke_end - ts_collect_start
	d_overhead = d_resolve + d_collect
	d_total = ts_invoke_end - ts_invoke_start

	assert d_resolve >= 0, f"D_resolve is negative: {d_resolve}"
	assert d_backend >= 0, f"D_backend is negative: {d_backend}"
	assert d_collect >= 0, f"D_collect is negative: {d_collect}"
	assert d_overhead >= 0, f"D_overhead is negative: {d_overhead}"
	assert d_total >= 0, f"D_total is negative: {d_total}"

	# Overhead must not exceed total
	assert d_overhead <= d_total, (
		f"D_overhead ({d_overhead}) > D_total ({d_total})"
	)


@pytest.mark.asyncio
async def test_cache_hit_first_and_second_call():
	"""cache_hit is False on first invocation, True on second."""
	events, observer = _collect_events()
	engine = FakeAsyncFlowEngine(observer=observer)
	orchestrator = LanGraphOrchestrator(engine=engine)

	async def noop_tool(x: str) -> dict:
		"""A no-op tool for testing."""
		return {"result": x}

	wrapped = orchestrator.hpc_task(noop_tool)

	# First call
	await wrapped.coroutine("first")
	resolve_events = [e for e in events if e["event"] == "tool_resolve_end"]
	assert len(resolve_events) == 1
	assert resolve_events[0]["cache_hit"] is False

	# Second call
	await wrapped.coroutine("second")
	resolve_events = [e for e in events if e["event"] == "tool_resolve_end"]
	assert len(resolve_events) == 2
	assert resolve_events[1]["cache_hit"] is True


@pytest.mark.asyncio
async def test_invocation_id_consistency():
	"""All events for a single invocation share the same invocation_id."""
	events, observer = _collect_events()
	engine = FakeAsyncFlowEngine(observer=observer)
	orchestrator = LanGraphOrchestrator(engine=engine)

	async def noop_tool(x: str) -> dict:
		"""A no-op tool for testing."""
		return {"result": x}

	wrapped = orchestrator.hpc_task(noop_tool)
	await wrapped.coroutine("hello")

	# Filter to invocation events (exclude wrap events which use wrap_id)
	invocation_events = [
		e for e in events if "invocation_id" in e
	]
	assert len(invocation_events) == 4  # invoke_start, resolve_end, collect_start, invoke_end

	invocation_ids = {e["invocation_id"] for e in invocation_events}
	assert len(invocation_ids) == 1, (
		f"Expected 1 invocation_id, got {len(invocation_ids)}: {invocation_ids}"
	)

	# And it should be a valid UUID
	the_id = invocation_ids.pop()
	uuid.UUID(the_id)  # raises ValueError if invalid


@pytest.mark.asyncio
async def test_tool_name_present_in_all_events():
	"""Every emitted event includes the correct tool_name."""
	events, observer = _collect_events()
	engine = FakeAsyncFlowEngine(observer=observer)
	orchestrator = LanGraphOrchestrator(engine=engine)

	async def my_special_tool(x: str) -> dict:
		"""A tool with a distinctive name."""
		return {"out": x}

	wrapped = orchestrator.hpc_task(my_special_tool)
	await wrapped.coroutine("test")

	for event in events:
		assert event["tool_name"] == "my_special_tool", (
			f"Event {event['event']} has tool_name={event.get('tool_name')!r}"
		)


@pytest.mark.asyncio
async def test_wrap_events_use_wrap_id():
	"""Wrap events carry a wrap_id instead of invocation_id."""
	events, observer = _collect_events()
	engine = FakeAsyncFlowEngine(observer=observer)
	orchestrator = LanGraphOrchestrator(engine=engine)

	async def noop_tool(x: str) -> dict:
		"""A no-op tool for testing."""
		return {"result": x}

	orchestrator.hpc_task(noop_tool)

	wrap_events = [
		e for e in events if e["event"] in ("tool_wrap_start", "tool_wrap_end")
	]
	assert len(wrap_events) == 2

	# Both wrap events share the same wrap_id
	assert wrap_events[0]["wrap_id"] == wrap_events[1]["wrap_id"]
	uuid.UUID(wrap_events[0]["wrap_id"])  # valid UUID


@pytest.mark.asyncio
async def test_multiple_invocations_different_ids():
	"""Each invocation gets a unique invocation_id."""
	events, observer = _collect_events()
	engine = FakeAsyncFlowEngine(observer=observer)
	orchestrator = LanGraphOrchestrator(engine=engine)

	async def noop_tool(x: str) -> dict:
		"""A no-op tool for testing."""
		return {"result": x}

	wrapped = orchestrator.hpc_task(noop_tool)

	await wrapped.coroutine("call1")
	await wrapped.coroutine("call2")

	invoke_starts = [
		e for e in events if e["event"] == "tool_invoke_start"
	]
	assert len(invoke_starts) == 2
	assert invoke_starts[0]["invocation_id"] != invoke_starts[1]["invocation_id"]
