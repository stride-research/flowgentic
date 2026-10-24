"""The retry loop's optional imports.

The default retryable exceptions include httpx's and aiohttp's when they are
installed. aiohttp reaches an install only through the ``academy`` extra, so a
decorated task must still run without it.
"""

# ruff: noqa: S101

import sys
from typing import Callable

import pytest

from flowgentic.langGraph.execution_wrappers import (
    AsyncFlowType,
    ExecutionWrappersLangraph,
)
from flowgentic.langGraph.fault_tolerance import LangraphToolFaultTolerance
from flowgentic.utils.telemetry.introspection import GraphIntrospector


class PassThroughFlow:
    """Stands in for AsyncFlow: a task is its function, called in place."""

    def function_task(
        self, func: Callable[..., object], **kwargs: object
    ) -> Callable[..., object]:
        return func


async def _task() -> str:
    return "done"


@pytest.mark.parametrize("module", ["aiohttp", "httpx"])
async def test_function_task_runs_without_optional_module(
    monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    monkeypatch.setitem(sys.modules, module, None)
    wrapper = ExecutionWrappersLangraph(PassThroughFlow(), GraphIntrospector())
    task = wrapper.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)(_task)

    assert await task() == "done"


def test_default_retryable_exceptions_skip_missing_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "aiohttp", None)

    retryable = LangraphToolFaultTolerance()._default_retryable_exceptions()

    assert {TimeoutError, ConnectionError, OSError} <= set(retryable)
    assert not any(t.__module__.startswith("aiohttp") for t in retryable)


def test_default_retryable_exceptions_skip_broken_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An installed module that fails to import is skipped like a missing one."""
    real_import = __import__

    def import_with_broken_aiohttp(name: str, *args: object, **kwargs: object):
        if name == "aiohttp":
            raise ImportError("aiohttp is installed but cannot be imported")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", import_with_broken_aiohttp)

    retryable = LangraphToolFaultTolerance()._default_retryable_exceptions()

    assert {TimeoutError, ConnectionError, OSError} <= set(retryable)


async def test_default_retryable_exceptions_are_computed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry loop works out the default retryable exceptions once."""
    fault_tolerance = LangraphToolFaultTolerance()
    calls = 0
    compute = fault_tolerance._default_retryable_exceptions

    def counting_compute() -> tuple[type, ...]:
        nonlocal calls
        calls += 1
        return compute()

    monkeypatch.setattr(
        fault_tolerance, "_default_retryable_exceptions", counting_compute
    )

    for _ in range(3):
        assert (
            await fault_tolerance.retry_async(_task, fault_tolerance.default_cfg, "t")
            == "done"
        )

    assert calls == 1
