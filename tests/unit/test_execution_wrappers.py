from typing import Callable

from flowgentic.langGraph.execution_wrappers import (AsyncFlowType,
                                                     ExecutionWrappersLangraph)
from flowgentic.utils.telemetry.introspection import GraphIntrospector


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
