"""Measure LoopRunner against GraphRunner on the agents-at-scale example's workload.

One run is one agent process with the example's parts: its tools (a task tool
that suspends while its task runs on RHAPSODY's thread pool, an MCP tool
through the tool service, an inline note), its scripted fleet of two
replicas, an in-memory store, and an agent host with all the agents live at
once. Only the runner changes: ``LoopRunner``, or ``GraphRunner`` over the
example's graph with ``delete_finished=True``, as the example runs it. A run
prints one JSON line: the host's wall time, the process's CPU time, the event
loop's lag, the collector's pauses, peak RSS, and the store's calls and bytes.

    python benchmarks/agent_loop.py --runner loop --agents 1000
    python benchmarks/agent_loop.py --runner graph --agents 5000 --workers 64 --stagger 0

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

from langchain_core.messages import HumanMessage

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
) -> dict:
    from rhapsody import Session

    client = ToolServiceClient(requests, replies, index=0)
    client.start()
    backend = await example.task_backend(workers, False)
    session = Session(backends=[backend])
    runtime = ToolRuntime(session, client)
    fleet = Fleet(
        [example.ScriptedModel(), example.ScriptedModel()],
        admission=TokenAdmission(tokens=40_000, requests=64),
    )
    store = CountingStore()
    if kind == "loop":
        from flowgentic.loop import LoopRunner

        runner: Any = LoopRunner(fleet, example.TOOLS, runtime=runtime, store=store)
        saver = None
    else:
        from flowgentic.langgraph import GraphRunner, KeyValueSaver, bind_tools

        saver = KeyValueSaver(store)
        tools = bind_tools(runtime, example.TOOLS)
        runner = GraphRunner(
            example.build_graph(fleet, tools, saver), delete_finished=True
        )

    answers: Counter[str] = Counter()
    suspensions = [0]

    def on_done(result: AgentResult, output: Any) -> None:
        fleet.agent_done(result.id)
        suspensions[0] += result.suspensions
        if output is not None:
            messages = output.messages if kind == "loop" else output["messages"]
            answers[messages[-1].content] += 1

    def input_for(i: int) -> dict:
        task = f"Find the best temperature for cand-{i:06d}"
        return {"messages": [HumanMessage(task)]}

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
        "runner_stats": runner.stats() if kind == "loop" else saver.stats(),
        "agent_seconds": report.states["agent_seconds_by_category"],
    }


def run_once(kind: str, agents: int, workers: int, interval: float) -> dict:
    example = load_example()
    with example.McpServer() as mcp:
        requests, replies = queue.Queue(), [queue.Queue()]
        with example.ToolService(mcp.url, requests, replies):
            out = asyncio.run(
                run_agents(example, kind, agents, workers, interval, requests, replies[0])
            )
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
    parser.add_argument("--runner", choices=["loop", "graph"], default="loop")
    parser.add_argument("--agents", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=8, help="RHAPSODY's threads")
    parser.add_argument(
        "--stagger", type=float, default=0.05, help="seconds between first-wave batches"
    )
    parser.add_argument("--repeat", type=int, default=0)
    parser.add_argument("--sizes", type=lambda s: [int(x) for x in s.split(",")], default=[1000, 5000])
    parser.add_argument("--runners", type=lambda s: s.split(","), default=["loop", "graph"])
    args = parser.parse_args()
    if args.repeat:
        repeat(args)
        return
    print(json.dumps(run_once(args.runner, args.agents, args.workers, args.stagger)))


if __name__ == "__main__":
    main()
