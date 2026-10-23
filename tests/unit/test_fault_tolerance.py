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
