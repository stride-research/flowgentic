"""Substitution experiment: the agentic demo with and without Flowgentic (R5).

Both versions run the same LangGraph application on AsyncFlow. The Flowgentic
version goes through ``LangraphIntegration`` and its decorators; the direct
version calls AsyncFlow itself. ``docs/evaluation/substitution.md`` cites these
tests as the evidence for each criterion.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
DEMO_DIR = REPOSITORY / "examples" / "ai-hpc-coupling"
RUNNER = DEMO_DIR / "agentic_campaign.py"
INTEGRATIONS = ("flowgentic", "direct")
APPLICATION_MODULES = {
    "flowgentic": "agentic_application",
    "direct": "direct_agentic_application",
}
TIMING_FIELDS = {"started", "finished", "duration"}
REPORT = Path("agent_execution_results") / "execution_summary.md"

if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))


@pytest.fixture(autouse=True, scope="module")
def restore_logging_levels() -> Any:
    """Undo the runner's import-time ``configure_logging()`` after this module."""
    names = ("", "asyncio", "flowgentic", "radical.adr", "radical.asyncflow")
    levels = {name: logging.getLogger(name).level for name in names}
    yield
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


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
) -> Any:
    """Open the runner's campaign context for one integration."""
    demo("campaign_common").reset_demo_state()
    if backends is None:
        backends = await demo("campaign_common").create_local_backends(4)
    return demo("agentic_campaign").open_campaign(
        integration,
        backends,
        settings,
        demo("agentic_support").RehearsalDecisionModel(),
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

    # Only the adapter writes the node-level Markdown report.
    assert (tmp_path / "flowgentic" / REPORT).is_file()
    assert not (tmp_path / "direct" / REPORT).exists()


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


# Criterion 3 (amendment A1): a simulation that keeps failing.
@pytest.mark.parametrize(
    ("integration", "expected_attempts"),
    (
        # The explicit policy only: 4 simulations x 2 attempts.
        ("direct", 8),
        # The same policy, inside Flowgentic's implicit node retry
        # (3 attempts), which re-runs the whole simulation batch.
        ("flowgentic", 24),
    ),
)
async def test_persistent_simulation_failure(
    integration: str,
    expected_attempts: int,
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

    assert len(attempts) == expected_attempts


# Criterion 3 (amendment A1): Flowgentic's default per-attempt timeout (30 s)
# also wraps graph nodes. Scaled down to 0.3 s here, a node that outlasts it is
# cancelled and re-run, simulations included; the direct version has no node
# timeout and completes.
@pytest.mark.parametrize(
    ("integration", "expected_starts"),
    (("direct", 4), ("flowgentic", 12)),
)
async def test_node_exceeding_the_default_timeout_is_rerun(
    integration: str,
    expected_starts: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flowgentic.langGraph import fault_tolerance

    def scaled_down_defaults(self: Any) -> None:
        self.default_cfg = fault_tolerance.RetryConfig(timeout_sec=0.3)

    monkeypatch.setattr(
        fault_tolerance.LangraphToolFaultTolerance, "__init__", scaled_down_defaults
    )

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
        if integration == "flowgentic":
            with pytest.raises(asyncio.TimeoutError):
                await application.graph.ainvoke(application.state)
        else:
            state = await application.graph.ainvoke(application.state)
            assert state["spent"] == 4

    assert len(starts) == expected_starts


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


# Criterion 7 (amendment A2): AsyncFlow's own OpenTelemetry spans. Opt-in:
# AsyncFlow 0.5.0 needs RHAPSODY's TelemetryManager, which neither AsyncFlow
# nor Flowgentic depends on. Run with
#   uv run --extra dev --with 'rhapsody-py[telemetry]==0.6.0' \
#       pytest -s tests/integration/test_substitution.py -k telemetry
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
    print(f"\n{integration}: {len(spans)} spans: {dict(sorted(names.items()))}")
    assert spans
