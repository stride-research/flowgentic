"""State and policy types shared by both AI-HPC demo implementations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from demo_support import SurrogateService
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from langgraph.graph.state import CompiledStateGraph


@dataclass(frozen=True)
class CampaignSettings:
    """Scientific and operational constraints shared by both implementations."""

    batch_size: int = 4
    budget: int = 24
    uncertainty_threshold: float = 0.25
    max_cycles: int = 8
    inject_failure: bool = True


class CampaignState(TypedDict):
    """Scientific, conversational, and execution state for one campaign."""

    messages: Annotated[list[BaseMessage], add_messages]
    implementation: str
    cycle: int
    center: float
    radius: float
    uncertainty: float
    best_x: float | None
    best_value: float | None
    budget: int
    spent: int
    candidates: list[float]
    results: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    service: SurrogateService
    decision: str


@dataclass
class CampaignApplication:
    """A compiled campaign graph and its persistent surrogate service."""

    graph: CompiledStateGraph
    state: CampaignState
    service: SurrogateService
    service_future: asyncio.Future[SurrogateService]
