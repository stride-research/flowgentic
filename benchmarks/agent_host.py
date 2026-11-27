"""Measure host scheduling with synthetic inputs, an immediate runner and no I/O.

By default the host runs the agents with ``run_indexed``; ``--full-report``
with ``run``; ``--submit`` gives them one at a time to a started host, as a
server does, ``--live`` at a time as slots free.

``--otel DIR`` configures the OpenTelemetry SDK into ``DIR``, as
``agent_loop.py --otel`` does, with the same options, so the CPU time can be
compared with a run without.

Beside the CPU time it prints how many passes CPython's collector made in
each generation during the run. The host runs with ``collector=None``, so it
leaves the collector as it is, and the CPU time follows where the full
passes (generation 2) fall in the collector's cycle: a few thousand objects
more or less on the heap can change their number, and the CPU time with it,
by several per cent. An A/B should compare runs with the same number of full
passes, or try more than one ``--live``.
"""

import argparse
import asyncio
import gc
import resource
import sys
import time

from flowgentic.host import Agent, AgentHost, Finished, HostReport, Stagger


class Immediate:
    async def start(self, agent: Agent) -> Finished:
        return Finished(None)


async def submit(host: AgentHost, indices: range, make_input, live: int) -> HostReport:
    """Submit the agents to a started host, as requests that each wait for
    their agent's end, with at most ``live`` waiting for a slot."""
    open_requests, room = 0, asyncio.Event()

    def answered(_future: asyncio.Future) -> None:
        nonlocal open_requests
        open_requests -= 1
        room.set()

    async with host:
        for i in indices:
            while open_requests >= 2 * live:
                room.clear()
                await room.wait()
            host.submit(Agent(str(i), make_input(i))).add_done_callback(answered)
            open_requests += 1
        return await host.stop()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents", type=int, default=100_000)
    parser.add_argument("--live", type=int, default=256)
    parser.add_argument("--input-bytes", type=int, default=1024)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--full-report", action="store_true")
    mode.add_argument("--submit", action="store_true")
    import _otel  # beside this script

    _otel.add_arguments(parser)
    args = parser.parse_args()
    if args.agents < 0 or args.input_bytes < 0:
        parser.error("agents and input-bytes must be nonnegative")
    flush = _otel.from_arguments(args)
    host = AgentHost(
        Immediate(),
        max_live_agents=args.live,
        collector=None,
        stagger=Stagger(batch=args.live, interval_s=0),
    )
    indices = range(args.agents)

    def make_input(i: int) -> bytes:
        return bytes(args.input_bytes)

    passes = [stats["collections"] for stats in gc.get_stats()]
    start = time.perf_counter()
    cpu = time.process_time()
    if args.full_report:
        report = await host.run(Agent(str(i), make_input(i)) for i in indices)
    elif args.submit:
        report = await submit(host, indices, make_input, args.live)
    else:
        report = await host.run_indexed(indices, make_input)
    elapsed = time.perf_counter() - start
    if flush is not None:
        flush()
    cpu = time.process_time() - cpu
    passes = [stats["collections"] - n for stats, n in zip(gc.get_stats(), passes)]
    if report.finished != args.agents:
        raise RuntimeError(f"finished {report.finished} of {args.agents}")
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    mib = rss / (1024**2 if sys.platform == "darwin" else 1024)
    print(
        f"{report.finished} finished in {elapsed:.3f} s, {cpu:.3f} s of CPU, "
        f"peak process RSS {mib:.1f} MiB; collector passes by generation "
        + ", ".join(f"{g}: {n}" for g, n in enumerate(passes))
    )


if __name__ == "__main__":
    asyncio.run(main())
