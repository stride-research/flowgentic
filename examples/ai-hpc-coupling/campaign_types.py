"""LangGraph state and application types used by the Flowgentic demos."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from campaign_common import CampaignSettings, SurrogateService
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from langgraph.graph.state import CompiledStateGraph


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
