"""Time actual LangGraph interruptions and resumes against a selected store."""

import argparse
import asyncio
import json
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from flowgentic.langgraph import KeyValueSaver
from flowgentic.store import MISSING, MemoryStore, RadexStore, WriteThroughStore


class State(TypedDict):
    index: int
    payload: bytes
    resumed: int


def pause(state: State) -> dict:
    return {"resumed": interrupt(state["index"])}


class CountCalls:
    def __init__(self, store):
        self.store = store
        self.calls = Counter()

    def get(self, key, default=MISSING):
        self.calls["get"] += 1
        return self.store.get(key, default)

    def put(self, key, value):
        self.calls["put"] += 1
        self.store.put(key, value)

    def contains(self, key):
        self.calls["contains"] += 1
        return self.store.contains(key)

    def delete(self, key):
        self.calls["delete"] += 1
        self.store.delete(key)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("radex", "memory"), required=True)
    parser.add_argument(
        "--descriptor-file",
        type=Path,
        help="file containing an active Dragon DDict's serialized descriptor",
    )
    parser.add_argument("--agents", type=int, default=1000)
    parser.add_argument("--window", type=int, default=256)
    parser.add_argument("--payload-bytes", type=int, default=1024)
    parser.add_argument("--cache-mib", type=int, default=0)
    args = parser.parse_args()
    if (
        args.agents < 1
        or args.window < 1
        or min(args.payload_bytes, args.cache_mib) < 0
    ):
        parser.error("agents and window must be positive and sizes must be nonnegative")
    if (args.backend == "radex") != (args.descriptor_file is not None):
        parser.error("a RaDEX run requires --descriptor-file; a memory run uses none")
    if args.backend == "radex":
        backing = RadexStore.attach(args.descriptor_file.read_text().strip())
    else:
        backing = MemoryStore()
    counted = CountCalls(backing)
    store = (
        WriteThroughStore(counted, max_bytes=args.cache_mib << 20)
        if args.cache_mib
        else counted
    )
    builder = StateGraph(State)
    builder.add_node("pause", pause)
    builder.add_edge(START, "pause")
    builder.add_edge("pause", END)
    saver = KeyValueSaver(store)
    graph = builder.compile(checkpointer=saver)
    verifier = KeyValueSaver(backing)
    run_id = uuid.uuid4().hex
    payload = bytes(args.payload_bytes)
    elapsed = 0.0
    timed_calls = Counter()
    for start in range(0, args.agents, args.window):
        indices = range(start, min(start + args.window, args.agents))
        configs = [
            {"configurable": {"thread_id": f"bench-{run_id}-{i}"}} for i in indices
        ]
        try:
            counted.calls.clear()
            t0 = time.perf_counter()
            async with asyncio.TaskGroup() as group:
                stops = [
                    group.create_task(
                        graph.ainvoke(
                            {"index": i, "payload": payload}, cfg, durability="exit"
                        )
                    )
                    for i, cfg in zip(indices, configs)
                ]
            for i, task in zip(indices, stops):
                if task.result()["__interrupt__"][0].value != i:
                    raise RuntimeError("unexpected interrupt")
            async with asyncio.TaskGroup() as group:
                resumes = [
                    group.create_task(
                        graph.ainvoke(Command(resume=i), cfg, durability="exit")
                    )
                    for i, cfg in zip(indices, configs)
                ]
            elapsed += time.perf_counter() - t0
            timed_calls.update(counted.calls)
            for i, cfg, task in zip(indices, configs, resumes):
                # Bypass the cache and verify the final checkpoint in the selected backend.
                saved = verifier.get_tuple(cfg).checkpoint["channel_values"]
                if (
                    task.result()["resumed"] != i
                    or saved["resumed"] != i
                    or saved["payload"] != payload
                ):
                    raise RuntimeError(f"unexpected checkpoint for agent {i}")
        finally:
            # The benchmark exclusively owns these names. Delete outside the timing interval.
            # Use the shared wrapper so deletion invalidates its entries.
            for cfg in configs:
                saver.delete_thread(cfg["configurable"]["thread_id"])
    print(
        json.dumps(
            {
                "backend": args.backend,
                "cache_mib": args.cache_mib,
                "agents": args.agents,
                "window": args.window,
                "payload_bytes": args.payload_bytes,
                "seconds": elapsed,
                "agents_per_second": args.agents / elapsed,
                "timed_backend_calls": dict(timed_calls),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
