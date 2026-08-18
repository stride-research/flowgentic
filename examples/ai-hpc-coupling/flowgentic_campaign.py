"""Command-line runner for the Flowgentic AI-HPC presentation demo.

The audience-facing workflow is in ``application.py``. This file only prepares
the local demonstration, selects the campaign controller, and writes evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from application import CampaignSettings, build_campaign, run_with_application_control
from demo_support import (
    TARGET,
    configure_logging,
    create_local_backends,
    max_compute_parallelism,
    reset_demo_state,
    write_summary,
)

from flowgentic.langGraph.main import LangraphIntegration

# Apply this before ``asyncio.run`` so selector startup stays out of the demo.
configure_logging()


async def run(args: argparse.Namespace) -> int:
    configure_logging(logging.DEBUG if args.verbose else logging.WARNING)
    reset_demo_state()

    settings = CampaignSettings(
        batch_size=args.batch_size,
        budget=args.budget,
        uncertainty_threshold=args.threshold,
        max_cycles=args.max_cycles,
        inject_failure=not args.no_failure,
    )
    backends = await create_local_backends(args.compute_workers)

    print("\nFlowgentic AI-HPC coupling demo")
    print("agent graph -> Flowgentic -> AsyncFlow -> named AI/compute backends")
    print(f"controller={args.controller}  target={TARGET}  budget={args.budget}\n")

    async with LangraphIntegration(backend=backends) as integration:
        application = await build_campaign(integration, settings)
        print(
            f"persistent AI service started: {application.service.instance_id} "
            f"(model loads={application.service.loads})"
        )

        if args.controller == "adr":
            # ADR remains optional; the application graph does not import it.
            from adr_control import run_with_adr_control

            state, stop_reason = await run_with_adr_control(
                application,
                settings,
                integration.flow,
            )
        else:
            state, stop_reason = await run_with_application_control(
                application,
                settings,
            )

        output_dir = Path(args.output_dir).resolve()
        (output_dir / "agent_execution_results").mkdir(parents=True, exist_ok=True)
        integration.agent_introspector._final_state = state
        integration.agent_introspector.generate_report(str(output_dir))
        summary_path = write_summary(
            output_dir,
            state,
            controller=args.controller,
            stop_reason=stop_reason,
        )

        parallelism = max_compute_parallelism(state["trace"])
        print("\nProof points")
        print(
            f"  same resident service: {application.service.instance_id} "
            f"(loads={application.service.loads})"
        )
        print("  named execution backends: ai, compute")
        print(f"  observed compute parallelism: {parallelism}")
        print(
            "  end-to-end state: "
            f"decision -> {state['spent']} simulations -> uncertainty "
            f"{state['uncertainty']:.4f}"
        )
        print(f"  stopped because: {stop_reason}")
        print(f"  machine-readable summary: {summary_path}")
        print(
            "  agent execution report: "
            f"{output_dir / 'agent_execution_results' / 'execution_summary.md'}"
        )

        application.service_future.cancel()

    return 0


def parse_args() -> argparse.Namespace:
    default_output = Path(__file__).resolve().parent / "demo_results"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--controller",
        choices=("application", "adr"),
        default="application",
        help="Who owns campaign-level stopping and iteration.",
    )
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
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
