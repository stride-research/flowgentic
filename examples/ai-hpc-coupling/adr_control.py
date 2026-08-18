"""ADR campaign control for the Flowgentic AI-HPC synthetic application.

Shows the distinction between agent-graph execution and campaign-level
observe-decide-act control.
"""

from __future__ import annotations

from typing import Any

from application import CampaignApplication, CampaignSettings, CampaignState
from demo_support import print_cycle
from radical.adr import Decision, Operator, Policy, act, decide, goals, observe
from radical.adr.goals import AnyGoal, Goal
from radical.adr.state import Snapshot
from radical.asyncflow import WorkflowEngine


async def run_with_adr_control(
    application: CampaignApplication,
    settings: CampaignSettings,
    engine: WorkflowEngine,
) -> tuple[CampaignState, str]:
    """Let ADR iterate the same agent graph until a campaign goal is met."""

    class CampaignOperator(Operator):
        def __init__(self) -> None:
            super().__init__(engine, max_cycles=settings.max_cycles)
            self._campaign_state = application.state

        @goals
        def stop_conditions(self) -> AnyGoal:
            return AnyGoal(
                [
                    Goal(
                        name="scientific_convergence",
                        metric="uncertainty",
                        threshold=settings.uncertainty_threshold,
                        direction="minimize",
                        inclusive=True,
                    ),
                    Goal(
                        name="budget_exhausted",
                        metric="spent",
                        threshold=float(settings.budget),
                        direction="maximize",
                        inclusive=True,
                    ),
                ],
                name="stop_condition",
            )

        @act
        async def execute_agent_cycle(
            self,
            campaign_state: CampaignState,
        ) -> CampaignState:
            return await application.graph.ainvoke(campaign_state)

        @observe
        def observe_campaign(self, snapshot: Snapshot) -> dict[str, Any]:
            completed_cycles = [
                value
                for key, value in snapshot.observations.items()
                if key.startswith("result.")
                and isinstance(value, dict)
                and "uncertainty" in value
            ]
            if completed_cycles:
                self._campaign_state = completed_cycles[-1]

            return {
                "campaign_state": self._campaign_state,
                "uncertainty": self._campaign_state["uncertainty"],
                "spent": self._campaign_state["spent"],
            }

    class CampaignPolicy(Policy):
        def __init__(self, operator: CampaignOperator) -> None:
            super().__init__()
            self.actions = operator.get_actions()

        @decide
        async def choose_next_cycle(self, observation: dict[str, Any]) -> Decision:
            state = observation["campaign_state"]
            if (
                state["uncertainty"] <= settings.uncertainty_threshold
                or state["spent"] >= state["budget"]
            ):
                return Decision(stop=True)

            return Decision(
                actions=[self.actions.execute_agent_cycle(campaign_state=state)]
            )

    operator = CampaignOperator()
    operator.policy = CampaignPolicy(operator)

    previous_cycle = application.state["cycle"]
    async for _snapshot in operator.run():
        if operator._campaign_state["cycle"] != previous_cycle:
            print_cycle(operator._campaign_state, "ADR")
            previous_cycle = operator._campaign_state["cycle"]

    state = operator._campaign_state
    if state["uncertainty"] <= settings.uncertainty_threshold:
        stop_reason = "ADR scientific uncertainty goal reached"
    elif state["spent"] >= state["budget"]:
        stop_reason = "ADR simulation budget goal reached"
    else:
        stop_reason = "ADR maximum cycles reached"

    return state, stop_reason
