import asyncio
from typing import Callable, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from flowgentic.langGraph.execution_wrappers import (AsyncFlowType,
                                                     ExecutionWrappersLangraph)
from flowgentic.utils.telemetry.introspection import GraphIntrospector

try:
    from langgraph.errors import NodeTimeoutError
except ImportError:  # LangGraph adds node timeouts in 1.2.
    NodeTimeoutError = None


class RecordingFlow:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def function_task(
        self, func: Callable[..., object], **kwargs: object
    ) -> Callable[..., object]:
        self.calls.append(("function_task", func.__name__, kwargs))
        return func

    def block(
        self, func: Callable[..., object], **kwargs: object
    ) -> Callable[..., object]:
        self.calls.append(("block", func.__name__, kwargs))
        return func


async def _task() -> str:
    return "done"


def test_named_backend_is_forwarded_to_asyncflow_task() -> None:
    flow = RecordingFlow()
    wrapper = ExecutionWrappersLangraph(flow, GraphIntrospector())

    wrapper.asyncflow(
        flow_type=AsyncFlowType.FUNCTION_TASK,
        backend="compute",
        capture_stdio=True,
    )(_task)

    assert flow.calls == [  # noqa: S101
        (
            "function_task",
            "_task",
            {"backend": "compute", "capture_stdio": True},
        )
    ]


def test_named_backend_is_forwarded_to_service_task() -> None:
    flow = RecordingFlow()
    wrapper = ExecutionWrappersLangraph(flow, GraphIntrospector())

    wrapper.asyncflow(
        flow_type=AsyncFlowType.SERVICE_TASK,
        backend="ai",
    )(_task)

    assert flow.calls == [  # noqa: S101
        (
            "function_task",
            "_task",
            {"service": True, "backend": "ai", "capture_stdio": False},
        )
    ]


def _failing_task(runs: list[str]) -> Callable[[], object]:
    async def unreachable() -> str:
        """Fail as a lost connection does."""
        runs.append("run")
        raise ConnectionError("connection lost")

    return unreachable


def _failing_node(runs: list[str]) -> Callable[[dict], object]:
    async def unreachable(state: dict) -> dict:
        runs.append("run")
        raise ConnectionError("connection lost")

    return unreachable


async def test_failing_function_task_runs_once() -> None:
    runs: list[str] = []
    wrapper = ExecutionWrappersLangraph(RecordingFlow(), GraphIntrospector())
    task = wrapper.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)(
        _failing_task(runs)
    )

    with pytest.raises(ConnectionError):
        await task()

    assert runs == ["run"]  # noqa: S101


async def test_failing_tool_runs_once() -> None:
    runs: list[str] = []
    wrapper = ExecutionWrappersLangraph(RecordingFlow(), GraphIntrospector())
    agent_tool = wrapper.asyncflow(flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION)(
        _failing_task(runs)
    )

    with pytest.raises(ConnectionError):
        await agent_tool.ainvoke({})

    assert runs == ["run"]  # noqa: S101


async def test_failing_block_runs_once() -> None:
    runs: list[str] = []
    wrapper = ExecutionWrappersLangraph(RecordingFlow(), GraphIntrospector())
    node = wrapper.asyncflow(flow_type=AsyncFlowType.EXECUTION_BLOCK)(
        _failing_node(runs)
    )

    with pytest.raises(ConnectionError):
        await node({})

    assert runs == ["run"]  # noqa: S101


def test_retry_is_not_an_option() -> None:
    wrapper = ExecutionWrappersLangraph(RecordingFlow(), GraphIntrospector())

    with pytest.raises(TypeError):
        wrapper.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK, retry=object())


def test_tool_description_is_forwarded() -> None:
    wrapper = ExecutionWrappersLangraph(RecordingFlow(), GraphIntrospector())

    agent_tool = wrapper.asyncflow(
        flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION,
        tool_description="Report that the work is done.",
    )(_task)

    assert agent_tool.description == "Report that the work is done."  # noqa: S101


class NodeState(TypedDict):
    done: bool


async def test_langgraph_retries_a_block_node_by_its_own_policy() -> None:
    runs: list[str] = []
    wrapper = ExecutionWrappersLangraph(RecordingFlow(), GraphIntrospector())

    # --8<-- [start:node-retry-policy]
    async def simulate_batch(state: NodeState) -> dict:
        runs.append("run")
        if len(runs) == 1:
            raise ConnectionError("connection lost")
        return {"done": True}

    node = wrapper.asyncflow(flow_type=AsyncFlowType.EXECUTION_BLOCK)(simulate_batch)

    builder = StateGraph(NodeState)
    builder.add_node(
        "simulate_batch",
        node,
        retry_policy=RetryPolicy(
            max_attempts=2, initial_interval=0.0, retry_on=ConnectionError
        ),
    )
    # --8<-- [end:node-retry-policy]
    builder.add_edge(START, "simulate_batch")
    builder.add_edge("simulate_batch", END)

    state = await builder.compile().ainvoke({"done": False})

    assert state == {"done": True}  # noqa: S101
    assert runs == ["run", "run"]  # noqa: S101


@pytest.mark.skipif(NodeTimeoutError is None, reason="needs LangGraph 1.2")
async def test_langgraph_times_out_a_block_node() -> None:
    wrapper = ExecutionWrappersLangraph(RecordingFlow(), GraphIntrospector())

    async def slow_batch(state: NodeState) -> dict:
        await asyncio.sleep(10)
        return {"done": True}

    node = wrapper.asyncflow(flow_type=AsyncFlowType.EXECUTION_BLOCK)(slow_batch)
    builder = StateGraph(NodeState)
    builder.add_node("slow_batch", node, timeout=0.05)
    builder.add_edge(START, "slow_batch")
    builder.add_edge("slow_batch", END)

    with pytest.raises(NodeTimeoutError):
        await builder.compile().ainvoke({"done": False})
