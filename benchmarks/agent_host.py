"""Measure host scheduling with synthetic inputs, an immediate runner and no I/O."""

import argparse
import asyncio
import resource
import sys
import time

from flowgentic.host import Agent, AgentHost, Finished, Stagger


class Immediate:
    async def start(self, agent: Agent) -> Finished:
        return Finished(None)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents", type=int, default=100_000)
    parser.add_argument("--live", type=int, default=256)
    parser.add_argument("--input-bytes", type=int, default=1024)
    parser.add_argument("--full-report", action="store_true")
    args = parser.parse_args()
    if args.agents < 0 or args.input_bytes < 0:
        parser.error("agents and input-bytes must be nonnegative")
    host = AgentHost(
        Immediate(),
        max_live_agents=args.live,
        collector=None,
        stagger=Stagger(batch=args.live, interval_s=0),
    )
    indices = range(args.agents)

    def make_input(i: int) -> bytes:
        return bytes(args.input_bytes)

    start = time.perf_counter()
    if args.full_report:
        report = await host.run(Agent(str(i), make_input(i)) for i in indices)
    else:
        report = await host.run_indexed(indices, make_input)
    elapsed = time.perf_counter() - start
    if report.finished != args.agents:
        raise RuntimeError(f"finished {report.finished} of {args.agents}")
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    mib = rss / (1024**2 if sys.platform == "darwin" else 1024)
    print(
        f"{report.finished} finished in {elapsed:.3f} s, peak process RSS {mib:.1f} MiB"
    )


if __name__ == "__main__":
    asyncio.run(main())
