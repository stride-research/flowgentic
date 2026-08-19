"""Run the agent-augmented RADICAL AI-HPC presentation demo."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from agentic_support import (
    AgentDecisionModel,
    LangChainDecisionModel,
    RehearsalDecisionModel,
    print_agentic_cycle,
)
from augmented_agents import build_langgraph_agent_components
from augmented_application import (
    build_augmented_campaign,
    run_augmented_with_application_control,
)
from campaign_common import (
    TARGET,
    CampaignSettings,
    configure_logging,
    create_local_backends,
    max_compute_parallelism,
    reset_demo_state,
    write_summary,
)
from dotenv import load_dotenv
from radical.asyncflow import WorkflowEngine

from flowgentic.utils.llm_providers import ChatLLMProvider

configure_logging()


def build_decision_model(args: argparse.Namespace) -> AgentDecisionModel:
    """Create reliable rehearsal agents or live LLM-backed components."""
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


async def run(args: argparse.Namespace) -> int:
    """Execute the augmented campaign and write presentation evidence."""
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
    agent_components = build_langgraph_agent_components(decision_model)
    backends = await create_local_backends(args.compute_workers)

    print("\nAgent-augmented RADICAL AI-HPC coupling demo")
    print("application/ADR -> AsyncFlow <-> Flowgentic <-> agent components")
    print(
        f"controller={args.controller}  agent_mode={args.agent_mode}  "
        f"model={decision_model.name}"
    )
    print(f"target={TARGET}  budget={args.budget}\n")

    flow = await WorkflowEngine.create(backend=backends)
    application = None
    try:
        application = await build_augmented_campaign(flow, settings, agent_components)
        print(
            f"persistent AI service started: {application.service.instance_id} "
            f"(model loads={application.service.loads})"
        )

        if args.controller == "adr":
            from adr_control import run_with_adr_control

            state, stop_reason = await run_with_adr_control(
                application,
                settings,
                flow,
                cycle_printer=print_agentic_cycle,
                cycle_executor=application.run_cycle,
            )
        else:
            state, stop_reason = await run_augmented_with_application_control(
                application,
                settings,
            )

        output_dir = Path(args.output_dir).resolve()
        summary_path = write_summary(
            output_dir,
            state,
            controller=args.controller,
            stop_reason=stop_reason,
        )

        print("\nProof points")
        print("  application and scientific cycle remain direct AsyncFlow")
        print("  LangGraph is confined to the three agent components")
        print("  Flowgentic binds only agent components to the ai backend")
        parallelism = max_compute_parallelism(state["trace"])
        print(f"  observed compute parallelism: {parallelism}")
        print(f"  guarded agent decisions recorded: {len(state['agent_trace'])}")
        print(f"  stopped because: {stop_reason}")
        print(f"  machine-readable summary: {summary_path}")
    finally:
        if application is not None:
            application.service_future.cancel()
        await flow.shutdown()

    return 0


def parse_args() -> argparse.Namespace:
    """Parse presentation-demo controls."""
    default_output = Path(__file__).resolve().parent / "augmented_demo_results"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--controller",
        choices=("application", "adr"),
        default="application",
    )
    parser.add_argument(
        "--agent-mode",
        choices=("rehearsal", "live"),
        default="rehearsal",
    )
    parser.add_argument(
        "--provider",
        choices=("openrouter", "chatopenai", "ollama"),
        default="openrouter",
    )
    parser.add_argument("--model", default="google/gemini-2.5-flash")
    parser.add_argument("--temperature", type=float, default=0.4)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--compute-workers", type=int, default=4)
    parser.add_argument("--budget", type=int, default=24)
    parser.add_argument("--threshold", type=float, default=0.25)
    parser.add_argument("--max-cycles", type=int, default=8)
    parser.add_argument("--no-failure", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--output-dir", default=str(default_output))
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
