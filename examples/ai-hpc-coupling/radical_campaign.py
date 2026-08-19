"""Run the RADICAL-only deterministic AI-HPC presentation baseline."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from campaign_common import (
    TARGET,
    CampaignSettings,
    configure_logging,
    create_local_backends,
    max_compute_parallelism,
    reset_demo_state,
    write_summary,
)
from radical.asyncflow import WorkflowEngine
from radical_application import build_radical_campaign, run_with_application_control

configure_logging()


async def run(args: argparse.Namespace) -> int:
    """Execute one baseline campaign and emit presentation evidence."""
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

    print("\nRADICAL deterministic AI-HPC coupling baseline")
    print("deterministic application -> AsyncFlow -> AI/compute backends")
    print(f"controller={args.controller}  target={TARGET}  budget={args.budget}\n")

    flow = await WorkflowEngine.create(backend=backends)
    application = None
    try:
        application = await build_radical_campaign(flow, settings)
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
                cycle_executor=application.run_cycle,
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
        print("  no agent framework and no Flowgentic adapter")
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
    finally:
        if application is not None:
            application.service_future.cancel()
        await flow.shutdown()

    return 0


def parse_args() -> argparse.Namespace:
    """Parse presentation-safe baseline options."""
    default_output = Path(__file__).resolve().parent / "radical_demo_results"
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
        help="Show detailed AsyncFlow lifecycle logs.",
    )
    parser.add_argument("--output-dir", default=str(default_output))
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
