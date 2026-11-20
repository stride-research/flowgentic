"""A campaign of agents on Flowgentic's own loop, in agent hosts, offline, on a
laptop.

Each agent looks its candidate up through an MCP tool, runs a simulation as a
RHAPSODY task and suspends while it runs, takes a note inline, and answers.

The parts are Flowgentic's, and everything around them stands in for an HPC
allocation:

- a scripted model, as a transport, behind a ``Fleet`` of two replicas, for
  model servers (``OpenAITransport`` replicas);
- RHAPSODY on a thread pool, for a RHAPSODY session on Dragon;
- ``MemoryStore()``, for ``RadexStore.attach(...)``;
- the tool service in a thread of this process, for the tool service in a
  process of its own;
- an MCP server in a process of its own, on 127.0.0.1, for MCP servers on the
  allocation.

With ``--processes 1`` (the default) the agents run in this process, beside
the tool service, which answers them over ``queue.Queue``. With more, each
agent process runs its own ``AgentHost`` on its share of the agents, and they
all share the one tool service over ``multiprocessing`` queues, as agent
processes share it over Dragon's queues on an allocation.

    python examples/agents-at-scale/campaign.py --agents 1000
    python examples/agents-at-scale/campaign.py --agents 1000 --processes 2

It needs the ``agents`` extra, and no LangGraph. With ``--runner graph`` the
same agents run as a LangGraph graph, ``campaign_langgraph.py`` beside this
file, with the ``langgraph`` extra too:

    python examples/agents-at-scale/campaign.py --runner graph --agents 1000

With ``--dragon``, under ``dragon``, the same campaign runs on Dragon's
resources: the agents' state in RaDEX, on a Dragon distributed dictionary from
RHAPSODY's ``DragonDataBackend``; the simulations on RHAPSODY's native Dragon
backend; the tool service and each agent process in a Dragon process of its
own, over Dragon's queues. It also needs Dragon, RaDEX's Python client, and a
RHAPSODY with ``DragonNativeExecutionBackend``. Only the model stays scripted.

    dragon examples/agents-at-scale/campaign.py --dragon --agents 1000 --processes 2
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import multiprocessing
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel

from flowgentic.fleet import Fleet, Reply, TokenAdmission, ToolCall, Usage
from flowgentic.host import AgentHost, AgentResult, Stagger
from flowgentic.loop import LoopRunner
from flowgentic.store import MemoryStore, RadexStore
from flowgentic.tools import ToolRuntime, mcp_tool, task_tool
from flowgentic.tools.mcp import SupervisedMcpPool
from flowgentic.tools.service import ToolServiceClient, serve

# --- the tools -----------------------------------------------------------------


def simulate(candidate: str, temperature: float) -> dict:
    """Simulate a candidate at a temperature; return its score."""
    time.sleep(0.01)  # the work
    score = 1.0 - abs(temperature - 500.0) / 1000.0
    return {"candidate": candidate, "temperature": temperature, "score": round(score, 4)}


class LookupArgs(BaseModel):
    candidate: str


async def note(text: str) -> str:
    """Write a note in the agent's log."""
    return f"noted: {text}"


# --8<-- [start:tools]
TOOLS = [
    # A RHAPSODY task; the agent suspends while it runs.
    task_tool(simulate, suspend=True),
    # An MCP tool, through the tool service. A lookup can safely run twice.
    mcp_tool(
        "lookup",
        description="Reference data for a candidate.",
        args_schema=LookupArgs,
        idempotent=True,
    ),
    # Inline, in the agent's process: a plain function.
    note,
]
# --8<-- [end:tools]


# --- the MCP server, in a process of its own --------------------------------------


def serve_mcp(port: int) -> None:
    """Run an MCP server with one tool, ``lookup``, on 127.0.0.1:``port``."""
    import uvicorn
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("reference-data", log_level="WARNING")

    @server.tool()
    async def lookup(candidate: str) -> dict:
        """Reference data for a candidate: a temperature to start from."""
        await asyncio.sleep(0.005)
        return {"candidate": candidate, "start_temperature": 480.0}

    app = server.streamable_http_app(
        stateless_http=True, json_response=True, host="127.0.0.1"
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


class McpServer:
    """``serve_mcp`` in a process of its own, as MCP servers run outside the
    agent processes on an allocation."""

    def __enter__(self) -> McpServer:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/mcp"
        self._proc = subprocess.Popen(
            [sys.executable, __file__, "--mcp-server", str(port)]
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                return self
            except OSError:
                if self._proc.poll() is not None or time.monotonic() > deadline:
                    self._proc.kill()
                    raise RuntimeError("the MCP server did not start") from None
                time.sleep(0.05)

    def __exit__(self, *exc: object) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()


# --- the tool service, in a thread of this process ----------------------------------


class ToolService:
    """The tool service in a thread: ``serve`` on queues the caller made."""

    def __init__(self, url: str, requests: Any, replies: list) -> None:
        self.requests = requests
        self.stats: dict = {}
        self._ready = threading.Event()
        self._error: BaseException | None = None

        def run() -> None:
            pool = SupervisedMcpPool(url, clients=2, per_client=16)
            try:
                self.stats = asyncio.run(
                    serve(requests, replies, pool, on_ready=lambda _: self._ready.set())
                )
            except BaseException as exc:
                self._error = exc
                self._ready.set()

        self._thread = threading.Thread(target=run, daemon=True)

    def __enter__(self) -> ToolService:
        self._thread.start()
        if not self._ready.wait(30) or self._error is not None:
            raise RuntimeError("the tool service did not start") from self._error
        return self

    def __exit__(self, *exc: object) -> None:
        self.requests.put(None)  # stops the service
        self._thread.join(30)


def service_process(url: str, requests: Any, replies: list, status: Any) -> None:
    """The tool service's process: "ready" on ``status``, then its counters."""
    pool = SupervisedMcpPool(url, clients=2, per_client=16)
    try:
        stats = asyncio.run(
            serve(requests, replies, pool, on_ready=lambda _: status.put("ready"))
        )
    except BaseException as exc:
        status.put(f"failed: {type(exc).__name__}: {exc}")
        raise
    status.put(stats)


class ToolServiceProcess:
    """The tool service in a process of its own, as on an allocation."""

    def __init__(self, ctx: Any, url: str, requests: Any, replies: list) -> None:
        self.requests = requests
        self.stats: dict = {}
        self._status = ctx.Queue()
        self._proc = ctx.Process(
            target=service_process,
            args=(url, requests, replies, self._status),
            name="tool-service",
        )

    def __enter__(self) -> ToolServiceProcess:
        self._proc.start()
        ready = self._status.get(timeout=60)
        if ready != "ready":
            raise RuntimeError(f"the tool service did not start: {ready}")
        return self

    def __exit__(self, exc_type: object, *exc: object) -> None:
        self.requests.put(None)  # stops the service
        try:
            stats = self._status.get(timeout=60)
        finally:
            self._proc.join(30)
        if isinstance(stats, dict):
            self.stats = stats
        elif exc_type is None:  # the service failed; an error in flight says more
            raise RuntimeError(f"the tool service {stats}")


# --- a scripted model, standing in for the model servers -----------------------


class ScriptedModel:
    """A transport that looks the candidate up; simulates it at the reference
    temperature and notes that, in one turn; then answers with the score. On
    an allocation each replica is an ``OpenAITransport`` of a model server."""

    model = "scripted"
    base_url = None

    def __init__(self, latency_s: float = 0.005) -> None:
        self.latency_s = latency_s

    async def complete(
        self, messages: list[dict], tools: Any = None, **kwargs: Any
    ) -> Reply:
        await asyncio.sleep(self.latency_s)
        candidate = messages[0]["content"].split()[-1]
        names = {
            c["id"]: c["function"]["name"]
            for m in messages
            if m["role"] == "assistant"
            for c in m.get("tool_calls") or ()
        }
        done = {
            names.get(m["tool_call_id"]): m["content"]
            for m in messages
            if m["role"] == "tool"
        }
        calls: tuple = ()
        content = ""
        if "lookup" not in done:
            calls = (ToolCall("c1", "lookup", {"candidate": candidate}),)
        elif "simulate" not in done:
            start = json.loads(done["lookup"])["start_temperature"]
            calls = (
                ToolCall("c2", "simulate", {"candidate": candidate, "temperature": start}),
                ToolCall("c3", "note", {"text": f"simulating {candidate}"}),
            )
        else:
            content = f"best score {json.loads(done['simulate'])['score']}"
        # What a server would count: the prompt's size, and a short reply.
        prompt = 200 + sum(len(str(m.get("content") or "")) for m in messages) // 4
        return Reply(content, calls, usage=Usage(prompt, 20))


def langgraph_version() -> ModuleType:
    """The campaign's LangGraph version, ``campaign_langgraph.py`` beside this
    file: it needs the ``langgraph`` extra."""
    name = "agents_at_scale_langgraph"
    if name not in sys.modules:
        path = Path(__file__).with_name("campaign_langgraph.py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


# --- one agent process ----------------------------------------------------------


async def task_backend(workers: int, dragon: bool) -> Any:
    """RHAPSODY's backend for the task tools: a thread pool, or the native
    Dragon backend's pool of worker processes."""
    if not dragon:
        from rhapsody.backends import ConcurrentExecutionBackend

        return await ConcurrentExecutionBackend(ThreadPoolExecutor(workers))
    import cloudpickle
    from rhapsody.backends import DragonNativeExecutionBackend

    # RHAPSODY sends a task's function to its workers with cloudpickle, which
    # sends a function by name when its module looks importable. The workers
    # cannot import this script, so its functions go by value.
    if simulate.__module__ != "__main__":
        cloudpickle.register_pickle_by_value(sys.modules[simulate.__module__])
    return await DragonNativeExecutionBackend(workers_per_node=workers)


async def run_agents(
    index: int,
    processes: int,
    agents: int,
    requests: Any,
    replies: Any,
    workers: int = 8,
    descriptor: str | None = None,
    runner: str = "loop",
) -> dict:
    """Run agent process ``index``'s share of the campaign: agents ``index``,
    ``index + processes``, and so on. With a dictionary's ``descriptor``, on
    Dragon: the store is RaDEX, the tasks run on the native Dragon backend.
    ``runner="graph"``: the LangGraph version."""
    from rhapsody import Session

    dragon = descriptor is not None
    graph = langgraph_version() if runner == "graph" else None
    # --8<-- [start:process]
    client = ToolServiceClient(requests, replies, index=index)
    client.start()
    backend = await task_backend(workers, dragon)
    session = Session(backends=[backend])
    runtime = ToolRuntime(session, client)
    # Every process routes by the same hash, so each takes an equal share of
    # every replica's caps.
    replicas = [ScriptedModel(), ScriptedModel()] if graph is None else graph.replicas()
    fleet = Fleet(
        replicas,
        admission=TokenAdmission(tokens=40_000, requests=64),
        share=1 / processes,
    )
    # The agents' state: RaDEX on Dragon, this process's memory on a laptop.
    store = RadexStore.attach(descriptor) if dragon else MemoryStore()
    # --8<-- [end:process]
    if graph is None:
        # --8<-- [start:runner]
        # The loop puts an agent's state in the store when the agent suspends,
        # reads it back when it resumes, and deletes it when it ends.
        agent_runner = LoopRunner(fleet, TOOLS, runtime=runtime, store=store)
        # --8<-- [end:runner]
        state = agent_runner.stats
    else:
        agent_runner, saver = graph.graph_runner(fleet, runtime, store, TOOLS)
        state = saver.stats

    answers: Counter[str] = Counter()
    lifetimes = [0.0]
    suspensions = [0]

    def on_done(result: AgentResult, output: Any) -> None:
        if graph is not None:  # the loop tells the fleet itself
            fleet.agent_done(result.id)  # the agent makes no more model calls
        lifetimes[0] += result.t_end - result.t_start
        suspensions[0] += result.suspensions
        if output is not None:
            answers[answer(output)] += 1

    # --8<-- [start:host]
    host = AgentHost(
        agent_runner,
        max_live_agents=5_000,
        stagger=Stagger(batch=100, interval_s=0.05),
        on_done=on_done,
    )

    def input_for(i: int) -> dict:
        task = f"Find the best temperature for cand-{i:06d}"
        return {"messages": [{"role": "user", "content": task}]}

    try:
        report = await host.run_indexed(
            range(index, agents, processes), input_for, id_prefix="agent-"
        )
    finally:
        client.stop()
        await session.close()
    # --8<-- [end:host]
    fleet_stats = fleet.stats()
    return {
        "index": index,
        "pid": os.getpid(),
        "store": type(store).__name__,
        "task_backend": type(backend).__name__,
        "runner": runner,
        "agents": len(range(index, agents, processes)),
        "outcomes": report.outcomes,
        "errors": report.errors,
        "suspensions": suspensions[0],
        "answers": dict(answers),
        "wall_s": report.wall_s,
        "lifetimes_s": round(lifetimes[0], 3),
        "agent_seconds": report.states["agent_seconds_by_category"],
        "agent_seconds_by_state": {
            state: seconds
            for state, seconds in report.states["agent_seconds"].items()
            if state not in ("pending", "done")
        },
        "loop_lag": report.loop_lag,
        "collector": report.collector,
        "model_calls": fleet_stats["calls"],
        "model_calls_per_replica": fleet_stats["calls_per_replica"],
        "tasks": runtime.stats(),
        "state": state(),
        "tool_service_client": client.stats(),
    }


def answer(output: Any) -> str:
    """A finished agent's answer: the last message of the loop's ``LoopOutput``,
    or of a LangGraph graph's state."""
    if isinstance(output, dict):  # a graph's state, of LangChain messages
        return output["messages"][-1].content
    return output.messages[-1]["content"]


def agent_process(
    index: int,
    processes: int,
    agents: int,
    requests: Any,
    replies: Any,
    results: Any,
    workers: int,
    descriptor: str | None = None,
    runner: str = "loop",
) -> None:
    """An agent process's entry point: its share, reported on ``results``."""
    try:
        out = asyncio.run(
            run_agents(
                index, processes, agents, requests, replies, workers, descriptor, runner
            )
        )
    except BaseException as exc:
        results.put({"index": index, "error": f"{type(exc).__name__}: {exc}"})
        raise
    results.put(out)


# --- the campaign ---------------------------------------------------------------


def run_campaign(
    agents: int = 200,
    processes: int = 1,
    workers: int = 8,
    dragon: bool = False,
    runner: str = "loop",
) -> dict:
    """Run ``agents`` agents in ``processes`` agent processes against one tool
    service; return the campaign's summary. ``dragon=True`` runs it on Dragon,
    under ``dragon``. ``runner``: ``"loop"``, Flowgentic's own, or ``"graph"``,
    the LangGraph version."""
    if agents < 1 or processes < 1:
        raise ValueError("a campaign needs at least one agent and one process")
    if runner not in ("loop", "graph"):
        raise ValueError(f"no runner {runner!r}: loop or graph")
    if dragon:
        return run_campaign_on_dragon(agents, processes, workers, runner)
    t0 = time.monotonic()
    with McpServer() as mcp:
        if processes == 1:
            requests, replies = queue.Queue(), [queue.Queue()]
        else:
            ctx = multiprocessing.get_context("spawn")
            requests = ctx.Queue()
            replies = [ctx.Queue() for _ in range(processes)]
        with ToolService(mcp.url, requests, replies) as service:
            if processes == 1:
                parts = [
                    asyncio.run(
                        run_agents(
                            0, 1, agents, requests, replies[0], workers, None, runner
                        )
                    )
                ]
            else:
                results = ctx.Queue()
                procs = [
                    ctx.Process(
                        target=agent_process,
                        args=(
                            i, processes, agents, requests, replies[i], results,
                            workers, None, runner,
                        ),  # fmt: skip
                        name=f"agent-process-{i}",
                    )
                    for i in range(processes)
                ]
                for p in procs:
                    p.start()
                parts = _collect(results, procs)
    wall = time.monotonic() - t0
    return summarize(parts, service.stats, agents, wall)


def run_campaign_on_dragon(
    agents: int, processes: int, workers: int, runner: str = "loop"
) -> dict:
    """The campaign on Dragon: a dictionary for RaDEX, and the tool service and
    each agent process in a Dragon process, over Dragon's queues."""
    import dragon  # noqa: F401  # adds Dragon's start method to multiprocessing

    # Make "dragon" the start method, as Dragon's processes inherit it. An
    # import may have fixed the platform's default already, hence force.
    previous = multiprocessing.get_start_method(allow_none=True)
    multiprocessing.set_start_method("dragon", force=True)
    try:
        return _campaign_on_dragon(agents, processes, workers, runner)
    finally:
        multiprocessing.set_start_method(previous, force=True)


def _campaign_on_dragon(agents: int, processes: int, workers: int, runner: str) -> dict:
    from rhapsody.backends.data.dragon import DragonDataBackend

    # Importing RHAPSODY's backends loads Dragon's LLM engine, which clears
    # DRAGON_PATCH_MP. Dragon's processes need it to find their start method.
    os.environ["DRAGON_PATCH_MP"] = "True"
    ctx = multiprocessing.get_context("dragon")
    t0 = time.monotonic()
    # One dictionary for the agents' state: two managers of 1 GiB each.
    data = DragonDataBackend(
        name="agents", managers_per_node=2, n_nodes=1, total_mem=2 * 1024**3
    )
    asyncio.run(data.start())
    descriptor = data.endpoints[0].serialize()
    try:
        with McpServer() as mcp:
            requests = ctx.Queue()
            replies = [ctx.Queue() for _ in range(processes)]
            with ToolServiceProcess(ctx, mcp.url, requests, replies) as service:
                results = ctx.Queue()
                procs = [
                    ctx.Process(
                        target=agent_process,
                        args=(
                            i, processes, agents, requests, replies[i], results,
                            workers, descriptor, runner,
                        ),  # fmt: skip
                        name=f"agent-process-{i}",
                    )
                    for i in range(processes)
                ]
                for p in procs:
                    p.start()
                parts = _collect(results, procs)
    finally:
        asyncio.run(data.shutdown())
    wall = time.monotonic() - t0
    return summarize(parts, service.stats, agents, wall)


def _collect(results: Any, procs: list, timeout_s: float = 600.0) -> list[dict]:
    """Each agent process's summary, in process order; fail if one failed."""
    parts: dict[int, dict] = {}
    deadline = time.monotonic() + timeout_s
    while len(parts) < len(procs):
        try:
            part = results.get(timeout=1.0)
        except queue.Empty:
            dead = [p for p in procs if p.exitcode not in (None, 0)]
            if dead:
                raise RuntimeError(f"{dead[0].name} exited with {dead[0].exitcode}")
            if time.monotonic() > deadline:
                raise TimeoutError("the agent processes did not finish") from None
            continue
        if "error" in part:
            raise RuntimeError(f"agent process {part['index']} failed: {part['error']}")
        parts[part["index"]] = part
    for p in procs:
        p.join(30)
    return [parts[i] for i in sorted(parts)]


def summarize(parts: list[dict], service: dict, agents: int, wall_s: float) -> dict:
    """The campaign's totals over its agent processes."""

    def total(key: str) -> int:
        return sum(p[key] for p in parts)

    def merged(key: str) -> dict:
        out: Counter = Counter()
        for p in parts:
            out.update(p[key])
        return dict(out)

    seconds = merged("agent_seconds")
    by_state = merged("agent_seconds_by_state")
    lifetimes = sum(p["lifetimes_s"] for p in parts)
    return {
        "runner": parts[0]["runner"],
        "agents": agents,
        "processes": len(parts),
        "wall_s": round(wall_s, 3),
        # From the first agent's start to the last agent's end, in the slowest process.
        "hosts_wall_s": max(p["wall_s"] for p in parts),
        "outcomes": merged("outcomes"),
        "suspensions": total("suspensions"),
        "answers": merged("answers"),
        "agent_seconds": {k: round(v, 3) for k, v in seconds.items()},
        "agent_seconds_by_state": {k: round(v, 3) for k, v in by_state.items()},
        "lifetimes_s": round(lifetimes, 3),
        # The share of the agents' lifetimes that the three categories account for.
        "accounted": round(sum(seconds.values()) / lifetimes, 6) if lifetimes else None,
        "model_calls": total("model_calls"),
        "tasks": merged("tasks"),
        # Puts of an agent's state: the loop's, or LangGraph's checkpoints.
        "state_puts": sum(
            p["state"].get("store_puts", p["state"].get("checkpoints_put", 0))
            for p in parts
        ),
        "tool_service": {
            k: service.get(k) for k in ("received", "unique", "ok", "failed")
        },
        "per_process": parts,
    }


def report(summary: dict) -> str:
    """The summary in a few lines."""
    seconds = summary["agent_seconds"]
    active = sum(seconds.values()) or 1.0
    shares = ", ".join(
        f"{name.replace('_', ' ')} {v:.1f} s ({100 * v / active:.0f} %)"
        for name, v in seconds.items()
    )
    states = ", ".join(
        f"{name.replace('_', ' ')} {v:.1f} s"
        for name, v in summary["agent_seconds_by_state"].items()
    )
    lag = max(p["loop_lag"].get("max_ms", 0.0) for p in summary["per_process"])
    pauses = sum(p["collector"]["total_s"] for p in summary["per_process"])
    process_word = "process" if summary["processes"] == 1 else "processes"
    return "\n".join(
        [
            f"{summary['agents']} agents in {summary['processes']} agent "
            f"{process_word}: {summary['outcomes']}; the hosts ran for "
            f"{summary['hosts_wall_s']:.1f} s, the campaign {summary['wall_s']:.1f} s",
            f"answers: {summary['answers']}",
            f"agent-seconds: {shares}; "
            f"{100 * summary['accounted']:.2f} % of the agents' lifetimes",
            f"  by state: {states}",
            f"model calls: {summary['model_calls']}; tasks: "
            f"{summary['tasks']['submitted']} submitted, "
            f"{summary['tasks']['repeats']} asked for again on resume; "
            f"suspensions: {summary['suspensions']}; "
            f"state puts: {summary['state_puts']}; "
            f"tool service calls: {summary['tool_service']['unique']}",
            f"event loop lag at most {lag:.1f} ms; collector pauses {pauses:.3f} s",
        ]
    )


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--agents", type=int, default=200)
    p.add_argument("--processes", type=int, default=1)
    p.add_argument(
        "--workers",
        type=int,
        default=8,
        help="RHAPSODY's workers: threads per agent process, or with --dragon "
        "the native backend's workers per node",
    )
    p.add_argument(
        "--runner",
        choices=["loop", "graph"],
        default="loop",
        help="Flowgentic's own loop, or the LangGraph version (the langgraph extra)",
    )
    p.add_argument("--json", action="store_true", help="print the whole summary as JSON")
    p.add_argument(
        "--dragon",
        action="store_true",
        help="run on Dragon (RaDEX, the native backend), under `dragon`",
    )
    p.add_argument("--mcp-server", type=int, metavar="PORT", help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    if a.mcp_server:
        serve_mcp(a.mcp_server)
        return
    summary = run_campaign(a.agents, a.processes, a.workers, a.dragon, a.runner)
    print(json.dumps(summary, indent=2) if a.json else report(summary))


if __name__ == "__main__":
    main(sys.argv[1:])
