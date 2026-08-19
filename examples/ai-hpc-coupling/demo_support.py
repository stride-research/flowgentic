"""Flowgentic-specific support for the LangGraph AI-HPC demos.

Framework-neutral science, timing, resource setup, and reporting live in
``campaign_common.py`` so the RADICAL baseline does not import Flowgentic or an
agent framework.
"""

from __future__ import annotations

from typing import Any, Callable

from campaign_common import (
    INITIAL_CENTER,
    INITIAL_RADIUS,
    TARGET,
    SurrogateService,
    TransientSimulationError,
    configure_logging,
    create_local_backends,
    evaluate_candidate,
    initial_scientific_state,
    max_compute_parallelism,
    print_cycle,
    reset_demo_state,
    timed_event,
    write_summary,
)
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph

from flowgentic.langGraph.fault_tolerance import RetryConfig
from flowgentic.langGraph.main import LangraphIntegration

SIMULATION_RETRY = RetryConfig(
    max_attempts=2,
    base_backoff_sec=0.05,
    max_backoff_sec=0.05,
    jitter=0.0,
    timeout_sec=5.0,
    retryable_exceptions=(TransientSimulationError,),
)


def agent_message(content: str) -> AIMessage:
    """Create a report-friendly deterministic agent message."""
    return AIMessage(
        content=content,
        response_metadata={"model_name": "deterministic-surrogate"},
        usage_metadata={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    )


def initial_campaign_state(
    service: SurrogateService,
    budget: int,
) -> dict[str, Any]:
    """Create the initial structured state for the LangGraph demos."""
    state = initial_scientific_state(
        service,
        budget,
        implementation="deterministic",
    )
    state["messages"] = [
        HumanMessage(
            content=(
                "Find the best candidate within the simulation budget and "
                "reduce scientific uncertainty."
            )
        )
    ]
    return state


def record_plan(
    state: dict[str, Any],
    proposal: dict[str, Any],
) -> dict[str, Any]:
    """Add proposal results to graph state and demo evidence."""
    candidates = proposal["candidates"]
    return {
        "candidates": candidates,
        "trace": [*state["trace"], proposal["event"]],
        "decision": f"evaluate {len(candidates)} candidates",
        "messages": [
            agent_message(
                f"Cycle {state['cycle']}: surrogate {proposal['service_id']} "
                f"selected {len(candidates)} candidates."
            )
        ],
    }


def record_simulations(
    state: dict[str, Any],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Add concurrent simulation results to graph state and demo evidence."""
    return {
        "results": results,
        "spent": state["spent"] + len(results),
        "trace": [*state["trace"], *(item["event"] for item in results)],
        "messages": [
            agent_message(
                f"Executed {len(results)} simulations concurrently on compute."
            )
        ],
    }


def record_analysis(
    state: dict[str, Any],
    update: dict[str, Any],
) -> dict[str, Any]:
    """Advance the campaign with the latest surrogate-model update."""
    return {
        "cycle": state["cycle"] + 1,
        "center": update["best_x"],
        "radius": update["next_radius"],
        "uncertainty": update["uncertainty"],
        "best_x": update["best_x"],
        "best_value": update["best_value"],
        "trace": [*state["trace"], update["event"]],
        "decision": "continue or stop according to campaign goals",
        "messages": [
            agent_message(
                f"Best x={update['best_x']:.4f}; "
                f"uncertainty={update['uncertainty']:.4f}."
            )
        ],
    }


def add_instrumented_nodes(
    integration: LangraphIntegration,
    graph: StateGraph,
    nodes: dict[str, Callable[..., Any]],
) -> None:
    """Attach Flowgentic introspection without exposing demo bookkeeping."""
    introspector = integration.agent_introspector
    introspector._all_nodes = list(nodes)
    for name, node in nodes.items():
        graph.add_node(name, introspector.introspect_node(node, name))


__all__ = [
    "INITIAL_CENTER",
    "INITIAL_RADIUS",
    "SIMULATION_RETRY",
    "TARGET",
    "SurrogateService",
    "TransientSimulationError",
    "add_instrumented_nodes",
    "agent_message",
    "configure_logging",
    "create_local_backends",
    "evaluate_candidate",
    "initial_campaign_state",
    "max_compute_parallelism",
    "print_cycle",
    "record_analysis",
    "record_plan",
    "record_simulations",
    "reset_demo_state",
    "timed_event",
    "write_summary",
]
