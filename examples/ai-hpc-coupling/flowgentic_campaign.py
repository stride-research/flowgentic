"""Command-line runner for the Flowgentic AI-HPC presentation demo.

The audience-facing workflow is in ``application.py``. This file only prepares
the local demonstration, selects the campaign controller, and writes evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from application import build_campaign, run_with_application_control
from campaign_types import CampaignSettings
from demo_support import (
    TARGET,
    configure_logging,
    create_local_backends,
    max_compute_parallelism,
    reset_demo_state,
    write_summary,
)
from tracing import run_trace

from flowgentic.langGraph.main import LangraphIntegration


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

    print("\nFlowgentic deterministic AI-HPC coupling demo")
    print("deterministic graph -> Flowgentic -> AsyncFlow -> AI/compute backends")
    print(f"controller={args.controller}  target={TARGET}  budget={args.budget}\n")

    async with (
        run_trace(args.trace, "ai-hpc-deterministic-demo") as tracing,
        LangraphIntegration(backend=backends) as integration,
    ):
        if tracing is not None:
            await tracing.attach(integration.flow)
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
        if tracing is not None:
            print(f"  OpenTelemetry spans: {tracing.path}")

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
    parser.add_argument(
        "--trace",
        type=Path,
        metavar="PATH",
        help="Write the run's OpenTelemetry spans to PATH, one JSON object per line.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
