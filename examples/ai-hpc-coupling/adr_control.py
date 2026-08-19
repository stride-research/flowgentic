"""ADR campaign control shared by the AI-HPC synthetic applications.

Shows the distinction between application-cycle execution and campaign-level
observe-decide-act control. ADR can wrap either a compiled agent graph or the
direct AsyncFlow RADICAL baseline.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, cast

from campaign_common import CampaignSettings, print_cycle
from radical.adr import Decision, Operator, Policy, act, decide, goals, observe
from radical.adr.goals import AnyGoal, Goal
from radical.adr.state import Snapshot
from radical.asyncflow import WorkflowEngine


class InvokableGraph(Protocol):
    """Minimal graph interface used by the retained LangGraph applications."""

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]: ...


class CampaignApplication(Protocol):
    """State interface shared by direct and graph-based applications."""

    state: dict[str, Any]


class GraphApplication(CampaignApplication, Protocol):
    """Additional interface supplied by the retained LangGraph applications."""

    graph: InvokableGraph


async def run_with_adr_control(
    application: CampaignApplication,
    settings: CampaignSettings,
    engine: WorkflowEngine,
    cycle_printer: Callable[[dict[str, Any], str], None] = print_cycle,
    cycle_executor: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> tuple[dict[str, Any], str]:
    """Let ADR iterate a supplied cycle until a campaign goal is met."""
    if cycle_executor is None:
        cycle_executor = cast(GraphApplication, application).graph.ainvoke

    class CampaignOperator(Operator):
        def __init__(self) -> None:
            # ADR cycle 0 dispatches the first action; one final control cycle
            # is required to observe the last completed scientific cycle.
            super().__init__(engine, max_cycles=settings.max_cycles + 1)
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
        async def execute_campaign_cycle(
            self,
            campaign_state: dict[str, Any],
        ) -> dict[str, Any]:
            return await cycle_executor(campaign_state)

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
                or state["cycle"] >= settings.max_cycles
            ):
                return Decision(stop=True)

            return Decision(
                actions=[self.actions.execute_campaign_cycle(campaign_state=state)]
            )

    operator = CampaignOperator()
    operator.policy = CampaignPolicy(operator)

    previous_cycle = application.state["cycle"]
    async for _snapshot in operator.run():
        if operator._campaign_state["cycle"] != previous_cycle:
            cycle_printer(operator._campaign_state, "ADR")
            previous_cycle = operator._campaign_state["cycle"]

    state = operator._campaign_state
    if state["uncertainty"] <= settings.uncertainty_threshold:
        stop_reason = "ADR scientific uncertainty goal reached"
    elif state["spent"] >= state["budget"]:
        stop_reason = "ADR simulation budget goal reached"
    else:
        stop_reason = "ADR maximum cycles reached"

    return state, stop_reason
