"""Substitution experiment: the agentic demo with and without Flowgentic (R5).

Both versions run the same LangGraph application on AsyncFlow. The Flowgentic
version goes through ``LangraphIntegration`` and its decorators; the direct
version calls AsyncFlow itself. ``docs/evaluation/substitution.md`` cites these
tests as the evidence for each criterion. The last tests (R5b) drive LLM tool
selection and a supervisor handoff with a scripted chat model.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
import sys
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any, AsyncIterator

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import InjectedState, ToolNode, tools_condition
from langgraph.types import Command, Send

from flowgentic.langGraph.execution_wrappers import AsyncFlowType, BaseLLMAgentState

REPOSITORY = Path(__file__).resolve().parents[2]
DEMO_DIR = REPOSITORY / "examples" / "ai-hpc-coupling"
RUNNER = DEMO_DIR / "agentic_campaign.py"
INTEGRATIONS = ("flowgentic", "direct")
APPLICATION_MODULES = {
    "flowgentic": "agentic_application",
    "direct": "direct_agentic_application",
}
TIMING_FIELDS = {"started", "finished", "duration"}

if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))


def demo(module: str) -> ModuleType:
    """Import a demo module; imported lazily so a missing port fails per test."""
    return importlib.import_module(module)


def without_timing(value: Any) -> Any:
    """Drop wall-clock fields, which legitimately differ between runs."""
    if isinstance(value, dict):
        return {
            key: without_timing(item)
            for key, item in value.items()
            if key not in TIMING_FIELDS
        }
    if isinstance(value, list):
        return [without_timing(item) for item in value]
    return value


def one_cycle_settings(*, inject_failure: bool = False) -> Any:
    return demo("campaign_common").CampaignSettings(
        batch_size=4,
        budget=4,
        max_cycles=1,
        inject_failure=inject_failure,
    )


async def open_campaign(
    integration: str,
    settings: Any,
    backends: list[Any] | None = None,
    decision_model: Any = None,
) -> Any:
    """Open the runner's campaign context for one integration."""
    demo("campaign_common").reset_demo_state()
    if backends is None:
        backends = await demo("campaign_common").create_local_backends(4)
    return demo("agentic_campaign").open_campaign(
        integration,
        backends,
        settings,
        decision_model or demo("agentic_support").RehearsalDecisionModel(),
    )


def components_named(flow: Any, name: str) -> list[dict[str, Any]]:
    return [
        component
        for component in flow.components.values()
        if component["description"]["name"] == name
    ]


# Criteria 1, 4, 5 and 7: one full campaign per version, compared end to end.
def test_flowgentic_and_direct_versions_match(tmp_path: Path) -> None:
    summaries = {}
    for integration in INTEGRATIONS:
        output_dir = tmp_path / integration
        completed = subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "--agent-mode",
                "rehearsal",
                "--controller",
                "application",
                "--integration",
                integration,
                "--output-dir",
                str(output_dir),
            ],
            cwd=REPOSITORY,
            timeout=120,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, (integration, completed.stderr)
        for leak in ("never retrieved", "destroyed but it is pending"):
            assert leak not in completed.stderr, (integration, completed.stderr)
        summaries[integration] = json.loads(
            (output_dir / "campaign_summary.json").read_text()
        )

    flowgentic, direct = summaries["flowgentic"], summaries["direct"]
    assert flowgentic.pop("integration") == "flowgentic"
    assert direct.pop("integration") == "direct"

    # Failure injection is on: one transient simulation failure, one retry.
    assert flowgentic["execution"]["retries"] == 1
    assert flowgentic["execution"]["max_compute_parallelism"] == 4
    assert flowgentic["service"]["loads"] == 1
    assert without_timing(direct) == without_timing(flowgentic)


# Criterion 2: both versions submit the same AsyncFlow components.
async def test_both_versions_submit_the_same_asyncflow_components() -> None:
    census = {}
    for integration in INTEGRATIONS:
        async with await open_campaign(integration, one_cycle_settings()) as (
            application,
            flow,
            _,
        ):
            await application.graph.ainvoke(application.state)
            census[integration] = Counter(
                (
                    component["type"],
                    component["description"]["name"],
                    component["description"]["target_backend"],
                )
                for component in flow.components.values()
            )

    assert census["direct"] == census["flowgentic"]
    # flow.components is keyed by uid: 14 distinct uids, 13 per cycle plus the
    # resident service.
    assert sum(census["direct"].values()) == 14
    assert census["direct"][("task", "run_simulation", "compute")] == 4


# Criterion 5: AsyncFlow places each task on the backend the application named.
async def test_both_versions_place_tasks_on_the_named_backends() -> None:
    placement = {}
    for integration in INTEGRATIONS:
        backends = await demo("campaign_common").create_local_backends(4)
        async with await open_campaign(integration, one_cycle_settings(), backends) as (
            application,
            _,
            _,
        ):
            await application.graph.ainvoke(application.state)
            placement[integration] = {
                backend.name: Counter(task["name"] for task in backend.tasks.values())
                for backend in backends
            }

    assert placement["direct"] == placement["flowgentic"]
    assert placement["direct"]["compute"] == Counter({"run_simulation": 4})
    assert set(placement["direct"]["ai"]) == {
        "load_surrogate",
        "query_surrogate",
        "reason_about_plan",
        "update_surrogate",
        "reason_about_evidence",
        "coordinate_agents",
    }


# Criterion 3 (amendment A1): a simulation that keeps failing. Both versions
# apply only the application's policy: 4 simulations x 2 attempts. Up to
# 0.3.0, Flowgentic's implicit node retry re-ran the whole batch (24 attempts).
@pytest.mark.parametrize("integration", INTEGRATIONS)
async def test_persistent_simulation_failure(
    integration: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    common = demo("campaign_common")
    attempts = []

    async def always_fail(
        x: float,
        cycle: int,
        candidate_index: int,
        fail_once: bool = False,
    ) -> dict[str, Any]:
        attempts.append(candidate_index)
        raise common.TransientSimulationError("persistent simulation failure")

    monkeypatch.setattr(
        demo(APPLICATION_MODULES[integration]), "evaluate_candidate", always_fail
    )
    async with await open_campaign(integration, one_cycle_settings()) as (
        application,
        _,
        _,
    ):
        with pytest.raises(common.TransientSimulationError):
            await application.graph.ainvoke(application.state)
        # Let sibling simulations still in their retry finish failing.
        await asyncio.sleep(0.3)

    assert len(attempts) == 8


# Criterion 3 (amendment A1): a graph node runs once, however long its work
# takes. Up to 0.3.0, Flowgentic wrapped every node in a 30 s timeout; scaled
# down to 0.3 s, a node like this one was cancelled and re-run (12 starts
# against 4) until the cycle failed.
@pytest.mark.parametrize("integration", INTEGRATIONS)
async def test_slow_node_runs_once(
    integration: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = demo(APPLICATION_MODULES[integration])
    evaluate_candidate = module.evaluate_candidate
    starts = []

    async def slow_simulation(
        x: float,
        cycle: int,
        candidate_index: int,
        fail_once: bool = False,
    ) -> dict[str, Any]:
        starts.append(candidate_index)
        await asyncio.sleep(0.6)
        return await evaluate_candidate(x, cycle, candidate_index, fail_once)

    monkeypatch.setattr(module, "evaluate_candidate", slow_simulation)
    async with await open_campaign(integration, one_cycle_settings()) as (
        application,
        _,
        _,
    ):
        state = await application.graph.ainvoke(application.state)

    assert state["spent"] == 4
    assert len(starts) == 4


class OutOfBoundsPlanner:
    """Rehearsal agents whose planner proposes outside the bounds.

    With ``corrects``, it proposes the rehearsal batch once a re-ask tells it
    why its proposal was rejected.
    """

    name = "out-of-bounds-planner"

    def __init__(self, *, corrects: bool) -> None:
        self.corrects = corrects
        self.rehearsal = demo("agentic_support").RehearsalDecisionModel()
        self.feedback: list[list[dict[str, Any]]] = []

    async def plan(self, context: dict[str, Any]) -> Any:
        feedback = context.get("rejected_proposals", [])
        self.feedback.append(feedback)
        if self.corrects and feedback:
            return await self.rehearsal.plan(context)
        upper = float(context["upper_bound"])
        return demo("agentic_support").PlannerDecision(
            candidates=[upper + 1.0 + index for index in range(4)],
            rationale="Search beyond the trust region.",
        )

    async def analyze(self, context: dict[str, Any]) -> Any:
        return await self.rehearsal.analyze(context)

    async def supervise(self, context: dict[str, Any]) -> Any:
        return await self.rehearsal.supervise(context)


# The planner's proposals pass the same checks, re-ask and fallback in both
# versions: through accept in Flowgentic's, by hand in the direct one.
@pytest.mark.parametrize(
    ("corrects", "disposition", "rejections"),
    ((True, "accepted", 1), (False, "fallback", 2)),
    ids=("reask", "fallback"),
)
async def test_both_versions_accept_planner_proposals_alike(
    corrects: bool, disposition: str, rejections: int
) -> None:
    runs = {}
    for integration in INTEGRATIONS:
        planner = OutOfBoundsPlanner(corrects=corrects)
        async with await open_campaign(
            integration, one_cycle_settings(), decision_model=planner
        ) as (application, _, _):
            center, radius = application.state["center"], application.state["radius"]
            state = await application.graph.ainvoke(application.state)
        (entry,) = [e for e in state["agent_trace"] if e["agent"] == "planner"]
        runs[integration] = {
            "entry": entry,
            "simulated": [result["x"] for result in state["results"]],
            "events": [event["name"] for event in state["trace"]],
            "feedback": planner.feedback,
        }

    flowgentic, direct = runs["flowgentic"], runs["direct"]
    decision = flowgentic["entry"]["decision"]
    assert decision.get("disposition") == disposition
    rejected = decision["rejected_attempts"]
    assert len(rejected) == rejections
    assert {attempt["rejected_by"] for attempt in rejected} == {"within_bounds"}
    assert all("lie outside" in attempt["reason"] for attempt in rejected)
    # Each re-ask hands the planner the proposals rejected so far, and why
    planner_calls = 2 if disposition == "accepted" else rejections
    assert flowgentic["feedback"] == [rejected[:n] for n in range(planner_calls)]
    assert flowgentic["events"].count("agent.planner") == planner_calls
    # What ran is the accepted batch or the fallback's, within the bounds
    assert flowgentic["simulated"] == decision["candidates"]
    assert all(center - radius <= x <= center + radius for x in decision["candidates"])
    assert without_timing(direct) == without_timing(flowgentic)


# Criterion 3: cancelling a cycle while its simulations are in flight.
@pytest.mark.parametrize("integration", INTEGRATIONS)
async def test_cancelling_a_cycle_cancels_inflight_simulations(
    integration: str,
) -> None:
    async with await open_campaign(integration, one_cycle_settings()) as (
        application,
        flow,
        _,
    ):
        cycle = asyncio.create_task(application.graph.ainvoke(application.state))
        for _ in range(200):
            if len(components_named(flow, "run_simulation")) == 4:
                break
            await asyncio.sleep(0.01)
        simulations = components_named(flow, "run_simulation")
        assert len(simulations) == 4
        assert not any(sim["future"].done() for sim in simulations)

        cycle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cycle
        await asyncio.sleep(0.05)

        assert all(sim["future"].cancelled() for sim in simulations)


# Criterion 7 (amendment A2): AsyncFlow's own OpenTelemetry spans. AsyncFlow
# (0.5.0, 0.6.0) needs RHAPSODY's TelemetryManager, which neither AsyncFlow nor
# Flowgentic depends on; the dev extra installs it.
@pytest.mark.parametrize("integration", INTEGRATIONS)
async def test_asyncflow_telemetry_spans(integration: str) -> None:
    pytest.importorskip(
        "rhapsody.telemetry.manager",
        reason="AsyncFlow telemetry needs rhapsody-py[telemetry]",
    )
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    async with await open_campaign(integration, one_cycle_settings()) as (
        application,
        flow,
        _,
    ):
        await flow.start_telemetry(span_processors=[SimpleSpanProcessor(exporter)])
        async with flow.workflow_scope("substitution-cycle"):
            await application.graph.ainvoke(application.state)

    spans = exporter.get_finished_spans()
    names = Counter(span.name for span in spans)
    tasks = [span for span in spans if span.name == "task"]
    labels = {
        span.attributes["executable"]: span.attributes["backend"] for span in tasks
    }
    print(f"\n{integration}: {dict(sorted(names.items()))}; task backends: {labels}")

    # One cycle: the workflow scope, 4 node blocks and 9 tasks (the resident
    # service started before telemetry).
    assert names == Counter({"workflow": 1, "block": 4, "task": 9})
    for span in tasks:
        assert {"task_id", "executable", "status"} <= set(span.attributes)
        assert span.attributes["status"] == "completed"
    # AsyncFlow 0.5.0 and 0.6.0 label every task with the default backend, including
    # those that ran on "ai" (test_both_versions_place_tasks_on_the_named_backends).
    assert set(labels.values()) == {"compute"}


# --- R5b: LLM tool selection and handoff, driven by a scripted chat model ----
# The model's tool calls run through LangGraph's ToolNode, as they would for a
# live LLM. Both versions share the graph and its state schema: Flowgentic's
# BaseLLMAgentState (a plain Pydantic model with a messages channel) or
# LangGraph's MessagesState (a TypedDict, the shape create_agent uses).


@asynccontextmanager
async def open_engine(integration: str) -> AsyncIterator[tuple[Any, Any]]:
    """Yield an AsyncFlow engine and, for Flowgentic, its integration."""
    backends = await demo("campaign_common").create_local_backends(4)
    if integration == "flowgentic":
        from flowgentic.langGraph.main import LangraphIntegration

        async with LangraphIntegration(backend=backends) as adapter:
            yield adapter.flow, adapter
        return

    from radical.asyncflow import WorkflowEngine

    flow = await WorkflowEngine.create(backend=backends)
    try:
        yield flow, None
    finally:
        await flow.shutdown()


# --8<-- [start:agent-graph]
def agent_graph(
    model: GenericFakeChatModel,
    tools: list[BaseTool],
    state_schema: Any = BaseLLMAgentState,
) -> CompiledStateGraph:
    """A model node that calls tools through ToolNode until it answers."""

    async def call_model(state: Any) -> dict[str, Any]:
        messages = state["messages"] if isinstance(state, dict) else state.messages
        return {"messages": [await model.ainvoke(messages)]}

    graph = StateGraph(state_schema)
    graph.add_node("model", call_model)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", tools_condition)
    graph.add_edge("tools", "model")
    return graph.compile()
# --8<-- [end:agent-graph]


# --8<-- [start:science-tool]
def science_tool(integration: str, flow: Any, adapter: Any) -> BaseTool:
    """The demo's simulation, exposed as an agent tool on the compute backend."""

    async def run_simulation(x: float) -> dict[str, float]:
        """Evaluate one candidate with the demo's synthetic simulation."""
        result = await demo("campaign_common").evaluate_candidate(x, 0, 0)
        return {"x": result["x"], "value": result["value"]}

    description = "Run one bounded scientific simulation on compute."
    if integration == "flowgentic":
        return adapter.execution_wrappers.asyncflow(
            flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION,
            backend="compute",
            tool_description=description,
        )(run_simulation)
    direct = demo("direct_agentic_application")
    return direct.asyncflow_tool(description)(
        flow.function_task(run_simulation, backend="compute")
    )
# --8<-- [end:science-tool]


# --8<-- [start:handoff-tool]
def handoff_tool(
    integration: str, flow: Any, adapter: Any, agent_name: str
) -> BaseTool:
    """A tool that hands the task to another agent in the parent graph."""
    description = f"Hand the task to the {agent_name}."
    if integration == "flowgentic":
        return adapter.execution_wrappers.create_task_description_handoff_tool(
            agent_name, description
        )

    # Direct: the same Command as Flowgentic's tool, built in an AsyncFlow task.
    @flow.function_task
    async def hand_off(task_description: str, state: Any) -> Command:
        base_state = state if isinstance(state, dict) else state.model_dump()
        agent_input = {
            **base_state,
            "messages": [HumanMessage(content=task_description)],
        }
        return Command(goto=[Send(agent_name, agent_input)], graph=Command.PARENT)

    @tool(f"transfer_to_{agent_name}", description=description)
    async def transfer(
        task_description: Annotated[str, "What the next agent should do."],
        state: Annotated[Any, InjectedState],
    ) -> Command:
        return await hand_off(task_description, state)

    return transfer
# --8<-- [end:handoff-tool]


# Criterion 8: the model selects a tool, and the tool runs as an AsyncFlow task.
async def test_llm_selected_tool_runs_as_an_asyncflow_task() -> None:
    outcomes = {}
    for integration in INTEGRATIONS:
        model = GenericFakeChatModel(
            messages=iter(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "run_simulation",
                                "args": {"x": 2.75},
                                "id": "call-1",
                            }
                        ],
                    ),
                    AIMessage(content="The simulation at x=2.75 is done."),
                ]
            )
        )
        async with open_engine(integration) as (flow, adapter):
            graph = agent_graph(model, [science_tool(integration, flow, adapter)])
            final = await graph.ainvoke(
                {"messages": [HumanMessage(content="Evaluate x=2.75.")]}
            )
            census = Counter(
                (
                    component["description"]["name"],
                    component["description"]["target_backend"],
                )
                for component in flow.components.values()
            )
        messages = final["messages"]
        assert [type(message).__name__ for message in messages] == [
            "HumanMessage",
            "AIMessage",
            "ToolMessage",
            "AIMessage",
        ]
        assert messages[2].name == "run_simulation"
        assert messages[2].status == "success"
        assert census == Counter({("run_simulation", "compute"): 1})
        outcomes[integration] = messages[2].content

    assert outcomes["direct"] == outcomes["flowgentic"]
    assert "2.75" in outcomes["direct"]


# Criterion 9: a supervisor hands off to a worker through a handoff tool.
@pytest.mark.parametrize(
    "state_schema",
    (BaseLLMAgentState, MessagesState),
    ids=("pydantic-state", "typeddict-state"),
)
@pytest.mark.parametrize("integration", INTEGRATIONS)
async def test_supervisor_hands_off_to_a_worker(
    integration: str,
    state_schema: Any,
) -> None:
    model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "transfer_to_researcher",
                            "args": {"task_description": "Evaluate x=2.75."},
                            "id": "call-1",
                        }
                    ],
                )
            ]
        )
    )
    received = []

    async def researcher(state: Any) -> dict[str, Any]:
        # A Send payload reaches its node as sent: here, a plain mapping.
        received.append([message.content for message in state["messages"]])
        return {"messages": [AIMessage(content="researcher done")]}

    async with open_engine(integration) as (flow, adapter):
        supervisor = agent_graph(
            model,
            [handoff_tool(integration, flow, adapter, "researcher")],
            state_schema,
        )
        parent = StateGraph(state_schema)
        parent.add_node("supervisor", supervisor, destinations=("researcher",))
        parent.add_node("researcher", researcher)
        parent.add_edge(START, "supervisor")
        parent.add_edge("researcher", END)
        final = await parent.compile().ainvoke(
            {"messages": [HumanMessage(content="Plan the study.")]}
        )
        # The handoff itself runs as one AsyncFlow task in both versions.
        handoff_tasks = [
            component
            for component in flow.components.values()
            if component["type"] == "task"
        ]

    assert len(handoff_tasks) == 1
    assert received == [["Evaluate x=2.75."]]
    assert final["messages"][-1].content == "researcher done"
