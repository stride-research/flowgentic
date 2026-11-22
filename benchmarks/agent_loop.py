"""Measure LoopRunner against GraphRunner on the agents-at-scale example's workload.

One run is one agent process with the example's parts: its tools (a task tool
that suspends while its task runs on RHAPSODY's thread pool, an MCP tool
through the tool service, an inline note), a scripted fleet of two replicas,
an in-memory store, and an agent host with all the agents live at once. Only
the runner changes:

- ``loop``: ``LoopRunner`` on OpenAI-format messages, with no LangChain on its
  path, as the example runs it: its scripted model as a transport, and its
  note as a plain function;
- ``loop-langchain``: ``LoopRunner`` through LangChain's adapter: the
  LangGraph version's scripted chat model as the replicas, and the note as a
  LangChain tool;
- ``graph``: ``GraphRunner`` over the LangGraph version's graph, with
  ``delete_finished=True``, as ``--runner graph`` runs it.

``loop-langchain`` and ``graph`` need the ``langgraph`` extra.

A run prints one JSON line: the host's wall time, the process's CPU time, the
event loop's lag, the collector's pauses, peak RSS, and the store's calls and
bytes.

``--otel DIR`` configures the OpenTelemetry SDK, as the telemetry page does
at scale, to write metrics and spans into ``DIR``: a tenth of the agents keep
their spans, no exemplars, metrics every 10 s (``--otel-sample``,
``--otel-exemplars`` and ``--otel-interval`` change them; ``--no-spans``
exports metrics only). The CPU time then includes the SDK's, its exporter
threads' and the final flush, and the line adds the problems
``flowgentic.telemetry.read_export(DIR).check()`` finds: compare it with a
run without, at the same size. ``--otlp URL`` streams the same export over
OTLP/HTTP with ``flowgentic.otlp`` instead, or as well with ``--otel``; see
``otlp_sink.py``.

    python benchmarks/agent_loop.py --runner loop --agents 1000
    python benchmarks/agent_loop.py --runner graph --agents 5000 --workers 64 --stagger 0
    python benchmarks/agent_loop.py --agents 5000 --otel /tmp/otel-5000

``--repeat N`` runs each runner at each ``--sizes`` N times, interleaved, each
run in a fresh interpreter, and prints their lines. The example's own settings
are the defaults: 8 RHAPSODY workers, and the first wave started 100 agents
every 0.05 s.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import importlib.util
import json
import pickle  # nosec B403  # the benchmark's own values
import platform
import queue
import resource
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

from flowgentic.fleet import Fleet, TokenAdmission
from flowgentic.host import AgentHost, AgentResult, Stagger
from flowgentic.store import MemoryStore
from flowgentic.tools import ToolRuntime
from flowgentic.tools.service import ToolServiceClient

REPOSITORY = Path(__file__).resolve().parents[1]
EXAMPLE = REPOSITORY / "examples" / "agents-at-scale" / "campaign.py"
ANSWER = "best score 0.98"


def load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("agents_at_scale_campaign", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # RHAPSODY's workers find its functions
    spec.loader.exec_module(module)
    return module


class CountingStore(MemoryStore):
    """A ``MemoryStore`` that counts its calls, the bytes put, and the most
    bytes it held at once."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: Counter[str] = Counter()
        self.bytes_put = 0
        self.held = 0
        self.peak_held = 0

    def get(self, key: str, *default: Any) -> Any:
        self.calls["get"] += 1
        return super().get(key, *default)

    def put(self, key: str, value: Any) -> None:
        data = pickle.dumps(value, protocol=5)
        self.calls["put"] += 1
        self.bytes_put += len(data)
        with self._lock:
            old = self._d.get(key)
            self._d[key] = data
        self.held += len(data) - (len(old) if old is not None else 0)
        self.peak_held = max(self.peak_held, self.held)

    def delete(self, key: str) -> None:
        self.calls["delete"] += 1
        with self._lock:
            old = self._d.pop(key, None)
        if old is not None:
            self.held -= len(old)

    def contains(self, key: str) -> bool:
        self.calls["contains"] += 1
        return super().contains(key)


def _mib(maxrss: int) -> float:
    return round(maxrss / (1024**2 if sys.platform == "darwin" else 1024), 1)


async def run_agents(
    example: ModuleType,
    kind: str,
    agents: int,
    workers: int,
    interval: float,
    requests: Any,
    replies: Any,
    otel_flush: Any = None,
) -> dict:
    from rhapsody import Session

    client = ToolServiceClient(requests, replies, index=0)
    client.start()
    backend = await example.task_backend(workers, False)
    session = Session(backends=[backend])
    runtime = ToolRuntime(session, client)
    graph = None if kind == "loop" else example.langgraph_version()
    replicas: list = (
        [example.ScriptedModel(), example.ScriptedModel()]
        if graph is None
        else graph.replicas()
    )
    fleet = Fleet(replicas, admission=TokenAdmission(tokens=40_000, requests=64))
    store = CountingStore()
    if kind in ("loop", "loop-langchain"):
        from flowgentic.loop import LoopRunner

        tools = list(example.TOOLS)  # its note, a plain function
        if kind == "loop-langchain":
            from langchain_core.tools import tool

            tools[-1] = tool(tools[-1])  # the note as a LangChain tool
        runner: Any = LoopRunner(fleet, tools, runtime=runtime, store=store)
        saver = None
    else:
        runner, saver = graph.graph_runner(fleet, runtime, store, example.TOOLS)

    answers: Counter[str] = Counter()
    suspensions = [0]

    def on_done(result: AgentResult, output: Any) -> None:
        fleet.agent_done(result.id)
        suspensions[0] += result.suspensions
        if output is not None:
            answers[example.answer(output)] += 1

    def input_for(i: int) -> dict:
        task = f"Find the best temperature for cand-{i:06d}"
        return {"messages": [{"role": "user", "content": task}]}

    host = AgentHost(
        runner,
        max_live_agents=5_000,
        stagger=Stagger(batch=100, interval_s=interval),
        on_done=on_done,
    )
    gc.collect()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    cpu = time.process_time()
    t = time.perf_counter()
    try:
        report = await host.run_indexed(range(agents), input_for, id_prefix="agent-")
    finally:
        client.stop()
    wall = time.perf_counter() - t
    if otel_flush is not None:
        otel_flush()  # the spans still queued, and the last export
    cpu = time.process_time() - cpu
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    await session.close()
    return {
        "runner": kind,
        "agents": agents,
        "workers": workers,
        "stagger_interval_s": interval,
        "outcomes": report.outcomes,
        "errors": report.errors,
        "correct": answers.get(ANSWER, 0),
        "suspensions": suspensions[0],
        "wall_s": round(wall, 3),
        "host_wall_s": report.wall_s,
        "cpu_s": round(cpu, 3),
        "loop_lag": report.loop_lag,
        "collector": {
            k: report.collector[k] for k in ("pauses", "total_s") if k in report.collector
        },
        "peak_rss_mib": _mib(rss),
        "rss_before_mib": _mib(rss_before),
        "model_calls": fleet.stats()["calls"],
        "tasks": runtime.stats(),
        "store": {
            "calls": dict(store.calls),
            "bytes_put": store.bytes_put,
            "peak_bytes_held": store.peak_held,
            "keys_left": len(store.keys()),
        },
        "runner_stats": saver.stats() if kind == "graph" else runner.stats(),
        "agent_seconds": report.states["agent_seconds_by_category"],
    }


def run_once(
    kind: str,
    agents: int,
    workers: int,
    interval: float,
    otel: Path | None = None,
    spans: bool = True,
    **otel_options: Any,
) -> dict:
    """One run; with ``otel``, the SDK writes into it, as ``_otel.configure``
    sets it up with ``spans`` and ``otel_options``."""
    flush = None
    otlp = otel_options.get("otlp")
    if otel is not None or otlp is not None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import _otel

        flush = _otel.configure(otel, spans, **otel_options)
    example = load_example()
    with example.McpServer() as mcp:
        requests, replies = queue.Queue(), [queue.Queue()]
        with example.ToolService(mcp.url, requests, replies):
            out = asyncio.run(
                run_agents(
                    example, kind, agents, workers, interval, requests, replies[0], flush
                )
            )
    signals = "metrics+spans" if spans else "metrics"
    out["otel"] = None if otel is None else signals
    out["otlp"] = None if otlp is None else signals
    if otel is not None or otlp is not None:
        options = {
            "sample": _otel.SAMPLE,
            "exemplars": False,
            "interval_s": _otel.INTERVAL_S,
            **otel_options,
        }
        out["otel_options"] = options
    if otel is not None:
        out.update(_otel.summary(otel, sampled=spans and options["sample"] < 1))
    out["python"] = platform.python_version()
    out["machine"] = platform.machine()
    return out


def repeat(args: argparse.Namespace) -> None:
    """Each runner at each size, ``args.repeat`` times, interleaved, each in a
    fresh interpreter."""
    for rep in range(args.repeat):
        for agents in args.sizes:
            for kind in args.runners:
                completed = subprocess.run(
                    [
                        sys.executable,
                        __file__,
                        "--runner", kind,
                        "--agents", str(agents),
                        "--workers", str(args.workers),
                        "--stagger", str(args.stagger),
                    ],  # fmt: skip
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=1800,
                )
                if completed.returncode != 0:
                    sys.exit(completed.stderr)
                line = json.loads(completed.stdout.strip().splitlines()[-1])
                line["rep"] = rep
                print(json.dumps(line), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runner", choices=["loop", "loop-langchain", "graph"], default="loop")
    parser.add_argument("--agents", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=8, help="RHAPSODY's threads")
    parser.add_argument(
        "--stagger", type=float, default=0.05, help="seconds between first-wave batches"
    )
    import _otel  # beside this script

    _otel.add_arguments(parser)
    parser.add_argument("--repeat", type=int, default=0)
    parser.add_argument("--sizes", type=lambda s: [int(x) for x in s.split(",")], default=[1000, 5000])
    parser.add_argument(
        "--runners", type=lambda s: s.split(","), default=["loop", "loop-langchain", "graph"]
    )
    args = parser.parse_args()
    if args.repeat:
        repeat(args)
        return
    print(
        json.dumps(
            run_once(
                args.runner,
                args.agents,
                args.workers,
                args.stagger,
                args.otel,
                not args.no_spans,
                sample=args.otel_sample,
                exemplars=args.otel_exemplars,
                interval_s=args.otel_interval,
                otlp=args.otlp,
            )
        )
    )


if __name__ == "__main__":
    main()
