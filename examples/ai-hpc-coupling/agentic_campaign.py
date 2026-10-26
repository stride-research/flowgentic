"""Run the agentic Flowgentic AI-HPC presentation demo.

The audience-facing workflow is in ``agentic_application.py``. This runner
selects an offline rehearsal model or a live LLM, prepares local resources,
chooses application or ADR campaign control, and writes execution evidence.
``--integration direct`` runs the same agent graph on AsyncFlow without
Flowgentic (``direct_agentic_application.py``), with the same decisions,
resources, retry policy, service lifetime, and stopping rules.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from agentic_application import (
    build_agentic_campaign,
    run_agentic_with_application_control,
)
from agentic_support import (
    AgentDecisionModel,
    LangChainDecisionModel,
    RehearsalDecisionModel,
    print_agentic_cycle,
)
from campaign_types import CampaignApplication, CampaignSettings
from demo_support import (
    TARGET,
    configure_logging,
    create_local_backends,
    max_compute_parallelism,
    reset_demo_state,
    write_summary,
)
from direct_agentic_application import build_direct_agentic_campaign
from dotenv import load_dotenv
from radical.asyncflow import WorkflowEngine
from tracing import run_trace

from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.utils.llm_providers import ChatLLMProvider


def build_decision_model(args: argparse.Namespace) -> AgentDecisionModel:
    """Create either reliable rehearsal agents or genuinely LLM-backed agents."""
    if args.agent_mode == "rehearsal":
        return RehearsalDecisionModel()

    model = ChatLLMProvider(
        provider=args.provider,
        model=args.model,
        temperature=args.temperature,
    )
    return LangChainDecisionModel(
        model=model,
        name=f"{args.provider.lower()}:{args.model}",
    )


@asynccontextmanager
async def open_campaign(
    integration: str,
    backends: list,
    settings: CampaignSettings,
    decision_model: AgentDecisionModel,
) -> AsyncIterator[
    tuple[CampaignApplication, WorkflowEngine, LangraphIntegration | None]
]:
    """Build the agentic campaign through Flowgentic or on AsyncFlow directly.

    Yields the application, its workflow engine, and the Flowgentic integration
    (``None`` for the direct version). On exit the resident service is
    cancelled and the engine shut down.
    """
    async with AsyncExitStack() as stack:
        if integration == "flowgentic":
            # --8<-- [start:open-flowgentic]
            adapter = await stack.enter_async_context(
                LangraphIntegration(backend=backends)
            )
            flow = adapter.flow
            application = await build_agentic_campaign(
                adapter,
                settings,
                decision_model,
            )
            # --8<-- [end:open-flowgentic]
        else:
            adapter = None
            # --8<-- [start:open-direct]
            flow = await WorkflowEngine.create(backend=backends)
            stack.push_async_callback(flow.shutdown)
            application = await build_direct_agentic_campaign(
                flow,
                settings,
                decision_model,
            )
            # --8<-- [end:open-direct]
        stack.callback(application.service_future.cancel)
        yield application, flow, adapter


async def run(args: argparse.Namespace) -> int:
    """Execute one agentic campaign and generate presentation evidence."""
    configure_logging(logging.DEBUG if args.verbose else logging.WARNING)
    reset_demo_state()
    load_dotenv()

    settings = CampaignSettings(
        batch_size=args.batch_size,
        budget=args.budget,
        uncertainty_threshold=args.threshold,
        max_cycles=args.max_cycles,
        inject_failure=not args.no_failure,
    )
    decision_model = build_decision_model(args)
    backends = await create_local_backends(args.compute_workers)

    if args.integration == "flowgentic":
        print("\nFlowgentic agentic AI-HPC coupling demo")
        print("agent graph -> Flowgentic -> AsyncFlow -> AI/compute backends")
    else:
        print("\nAgentic AI-HPC coupling demo without Flowgentic")
        print("agent graph -> AsyncFlow -> AI/compute backends")
    print(
        f"controller={args.controller}  agent_mode={args.agent_mode}  "
        f"integration={args.integration}  model={decision_model.name}"
    )
    print(f"target={TARGET}  budget={args.budget}\n")

    async with (
        run_trace(args.trace, "ai-hpc-agentic-demo") as tracing,
        open_campaign(
            args.integration,
            backends,
            settings,
            decision_model,
        ) as (application, flow, integration),
    ):
        if tracing is not None:
            await tracing.attach(flow)
        print(
            f"persistent AI service started: {application.service.instance_id} "
            f"(model loads={application.service.loads})"
        )

        if args.controller == "adr":
            # ADR remains optional; the agent graph does not import it.
            from adr_control import run_with_adr_control

            state, stop_reason = await run_with_adr_control(
                application,
                settings,
                flow,
                cycle_printer=print_agentic_cycle,
            )
        else:
            state, stop_reason = await run_agentic_with_application_control(
                application,
                settings,
            )

        output_dir = Path(args.output_dir).resolve()
        summary_path = write_summary(
            output_dir,
            state,
            controller=args.controller,
            stop_reason=stop_reason,
            integration=args.integration,
        )

        parallelism = max_compute_parallelism(state["trace"])
        print("\nProof points")
        print(
            f"  same resident service: {application.service.instance_id} "
            f"(loads={application.service.loads})"
        )
        print("  bounded agent tools mapped to named backends: ai, compute")
        print(f"  observed compute parallelism: {parallelism}")
        print(
            "  agent decisions recorded: "
            f"{len(state.get('agent_trace', []))} with policy enforcement"
        )
        plans = [e["decision"] for e in state["agent_trace"] if e["agent"] == "planner"]
        accepted = sum(plan["disposition"] == "accepted" for plan in plans)
        rejected = sum(len(plan["rejected_attempts"]) for plan in plans)
        print(
            f"  planner decisions: {accepted} accepted, "
            f"{len(plans) - accepted} fallback, {rejected} proposals rejected"
        )
        print(
            "  end-to-end state: "
            f"reason -> {state['spent']} simulations -> uncertainty "
            f"{state['uncertainty']:.4f}"
        )
        print(f"  stopped because: {stop_reason}")
        print(f"  machine-readable summary: {summary_path}")
        if tracing is not None:
            print(f"  OpenTelemetry spans and events: {tracing.path}")

    return 0


def parse_args() -> argparse.Namespace:
    """Parse presentation-demo controls."""
    default_output = Path(__file__).resolve().parent / "agentic_demo_results"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--controller",
        choices=("application", "adr"),
        default="application",
        help="Who owns campaign-level stopping and iteration.",
    )
    parser.add_argument(
        "--integration",
        choices=("flowgentic", "direct"),
        default="flowgentic",
        help="Run the agent graph through Flowgentic or on AsyncFlow directly.",
    )
    parser.add_argument(
        "--agent-mode",
        choices=("rehearsal", "live"),
        default="rehearsal",
        help="Use offline scripted decisions or a live nondeterministic LLM.",
    )
    parser.add_argument(
        "--provider",
        choices=("openrouter", "chatopenai", "ollama"),
        default="openrouter",
        help="Live-mode LangChain chat-model provider.",
    )
    parser.add_argument(
        "--model",
        default="google/gemini-2.5-flash",
        help="Live-mode model name understood by the selected provider.",
    )
    parser.add_argument("--temperature", type=float, default=0.4)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--compute-workers", type=int, default=4)
    parser.add_argument("--budget", type=int, default=24)
    parser.add_argument("--threshold", type=float, default=0.25)
    parser.add_argument("--max-cycles", type=int, default=8)
    parser.add_argument(
        "--no-failure",
        action="store_true",
        help="Disable the recoverable failure used to demonstrate retry.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed Flowgentic and AsyncFlow lifecycle logs.",
    )
    parser.add_argument("--output-dir", default=str(default_output))
    parser.add_argument(
        "--trace",
        type=Path,
        metavar="PATH",
        help="Write the run's OpenTelemetry spans to PATH, one JSON object per line.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
