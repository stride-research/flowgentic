"""RADICAL-only baseline for the surrogate-guided AI-HPC campaign.

The application uses AsyncFlow directly: no agent framework and no Flowgentic
adapter are required for this deterministic adaptive workflow.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from campaign_common import (CampaignSettings, SurrogateService,
                             evaluate_candidate, initial_scientific_state,
                             print_cycle, retry_transient_simulation)
from radical.asyncflow import WorkflowEngine

CampaignState = dict[str, Any]
Cycle = Callable[[CampaignState], Awaitable[CampaignState]]


@dataclass
class RadicalApplication:
    """A direct AsyncFlow campaign and its persistent surrogate service."""

    state: CampaignState
    service: SurrogateService
    service_future: asyncio.Future[SurrogateService]
    run_cycle: Cycle


async def build_radical_campaign(
    flow: WorkflowEngine,
    settings: CampaignSettings,
) -> RadicalApplication:
    """Map the deterministic campaign directly onto AsyncFlow."""

    # 1. Start the same resident surrogate used by the agentic implementation.
    @flow.function_task(service=True, backend="ai")
    async def load_surrogate() -> SurrogateService:
        return await SurrogateService.load()

    service_future = load_surrogate()
    service = await service_future

    # 2. Map ordinary scientific capabilities directly onto named backends.
    @flow.function_task(backend="ai")
    async def propose_candidates(
        center: float,
        radius: float,
        cycle: int,
    ) -> dict[str, Any]:
        return await service.propose(center, radius, settings.batch_size, cycle)

    @flow.function_task(backend="compute")
    async def simulate(
        x: float,
        cycle: int,
        candidate_index: int,
        fail_once: bool = False,
    ) -> dict[str, Any]:
        return await evaluate_candidate(x, cycle, candidate_index, fail_once)

    @flow.function_task(backend="ai")
    async def update_surrogate(
        results: list[dict[str, Any]],
        radius: float,
        cycle: int,
    ) -> dict[str, Any]:
        return await service.assimilate(results, radius, cycle)

    # 3. Express one adaptive cycle with ordinary Python and AsyncFlow futures.
    async def run_cycle(state: CampaignState) -> CampaignState:
        proposal = await propose_candidates(
            state["center"], state["radius"], state["cycle"]
        )
        remaining = state["budget"] - state["spent"]
        candidates = proposal["candidates"][: max(0, remaining)]

        results = await asyncio.gather(
            *(
                retry_transient_simulation(
                    lambda x=x, index=index: simulate(
                        x,
                        state["cycle"],
                        index,
                        fail_once=(
                            settings.inject_failure
                            and state["cycle"] == 1
                            and index == 0
                        ),
                    )
                )
                for index, x in enumerate(candidates)
            )
        )
        update = await update_surrogate(results, state["radius"], state["cycle"])

        return {
            **state,
            "cycle": state["cycle"] + 1,
            "center": update["best_x"],
            "radius": update["next_radius"],
            "uncertainty": update["uncertainty"],
            "best_x": update["best_x"],
            "best_value": update["best_value"],
            "spent": state["spent"] + len(results),
            "candidates": candidates,
            "results": results,
            "trace": [
                *state["trace"],
                proposal["event"],
                *(result["event"] for result in results),
                update["event"],
            ],
            "decision": "continue or stop according to campaign goals",
        }

    state = initial_scientific_state(
        service,
        settings.budget,
        implementation="radical-baseline",
    )
    return RadicalApplication(state, service, service_future, run_cycle)


# 4. Campaign control can remain in the application or move unchanged to ADR.
async def run_with_application_control(
    application: RadicalApplication,
    settings: CampaignSettings,
) -> tuple[CampaignState, str]:
    """Iterate until the application converges or exhausts its hard limits."""
    state = application.state
    stop_reason = "maximum cycles reached"

    while state["cycle"] < settings.max_cycles and state["spent"] < state["budget"]:
        state = await application.run_cycle(state)
        print_cycle(state, "application")
        if state["uncertainty"] <= settings.uncertainty_threshold:
            stop_reason = "scientific uncertainty goal reached"
            break
    else:
        if state["spent"] >= state["budget"]:
            stop_reason = "simulation budget exhausted"

    return state, stop_reason
