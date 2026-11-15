"""A campaign of LangGraph agents in one agent host, offline, on a laptop.

Each agent looks its candidate up through an MCP tool, runs a simulation as a
RHAPSODY task and suspends while it runs, takes a note inline, and answers.
Everything runs in this process: a scripted model fleet instead of model
servers, RHAPSODY on a thread pool, the checkpoints in an in-memory store, and
the tool service in a thread with an MCP server on 127.0.0.1. On an HPC
allocation the same code takes real model servers, a RHAPSODY session on
Dragon, RaDEX and a tool service in a process of its own.

    python examples/agents-at-scale/campaign.py --agents 1000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pickle
import queue
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel

from flowgentic.fleet import Fleet, TokenAdmission
from flowgentic.host import Agent, AgentHost, Stagger
from flowgentic.langgraph import GraphRunner, KeyValueSaver, bind_tools, route_tool_calls
from flowgentic.store import MemoryStore
from flowgentic.tools import ToolRuntime, mcp_tool, task_tool
from flowgentic.tools.mcp import SupervisedMcpPool
from flowgentic.tools.service import ToolServiceClient, serve


# --- the tools ---------------------------------------------------------------------


def simulate(candidate: str, temperature: float) -> dict:
    """Simulate a candidate at a temperature; returns its score."""
    time.sleep(0.01)  # the work
    score = 1.0 - abs(temperature - 500.0) / 1000.0
    return {"candidate": candidate, "temperature": temperature, "score": round(score, 4)}


class LookupArgs(BaseModel):
    candidate: str


@tool
def note(text: str) -> str:
    """Write a note in the agent's log."""
    return f"noted: {text}"


# --- the MCP server, in a thread ---------------------------------------------------


def start_mcp_server() -> str:
    import uvicorn
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("reference-data", log_level="WARNING")

    @server.tool()
    async def lookup(candidate: str) -> dict:
        """Reference data for a candidate: a temperature to start from."""
        await asyncio.sleep(0.005)
        return {"candidate": candidate, "start_temperature": 480.0}

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = server.streamable_http_app(stateless_http=True, json_response=True, host="127.0.0.1")
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", timeout_keep_alive=600)
    uv = uvicorn.Server(config)
    threading.Thread(target=uv.run, daemon=True).start()
    deadline = time.time() + 10
    while not uv.started:
        if time.time() > deadline:
            raise RuntimeError("the MCP server did not start")
        time.sleep(0.02)
    return f"http://127.0.0.1:{port}/mcp"


def start_tool_service(url: str, processes: int) -> tuple[Any, list, threading.Thread, dict]:
    requests, replies = queue.Queue(), [queue.Queue() for _ in range(processes)]
    ready, out = threading.Event(), {}

    def run() -> None:
        pool = SupervisedMcpPool(url, clients=2, per_client=16)
        out["stats"] = asyncio.run(serve(requests, replies, pool, on_ready=lambda state: ready.set()))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    if not ready.wait(30):
        raise RuntimeError("the tool service did not start")
    return requests, replies, thread, out


# --- a scripted model, standing in for the model servers -----------------------------


class ScriptedReplica(BaseChatModel):
    """Looks the candidate up, simulates at the reference temperature, notes the
    score, and answers."""

    latency_s: float = 0.005

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedReplica:
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError

    async def _agenerate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        await asyncio.sleep(self.latency_s)
        candidate = messages[0].content.split()[-1]
        done = {m.name: m.content for m in messages if m.type == "tool"}
        if "lookup" not in done:
            calls = [{"name": "lookup", "args": {"candidate": candidate}, "id": "c1"}]
        elif "simulate" not in done:
            start = json.loads(done["lookup"])["start_temperature"]
            calls = [
                {"name": "simulate", "args": {"candidate": candidate, "temperature": start}, "id": "c2"},
                {"name": "note", "args": {"text": f"simulating {candidate}"}, "id": "c3"},
            ]
        else:
            return _result(AIMessage(f"best score {json.loads(done['simulate'])['score']}"))
        return _result(AIMessage("", tool_calls=calls))


def _result(message: AIMessage) -> ChatResult:
    message.usage_metadata = {"input_tokens": 200, "output_tokens": 20, "total_tokens": 220}
    return ChatResult(generations=[ChatGeneration(message=message)])


# --- the campaign ------------------------------------------------------------------


def build_graph(fleet: Fleet, tools: list, saver: KeyValueSaver):
    fleet = fleet.bind_tools(tools)

    async def model(state: MessagesState, config) -> dict:
        agent = config["configurable"]["thread_id"]
        return {"messages": [await fleet.ainvoke(agent, state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("model", model)
    g.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    g.add_edge(START, "model")
    g.add_conditional_edges("model", route_tool_calls, ["tools", "__end__"])
    g.add_edge("tools", "model")
    return g.compile(checkpointer=saver)


async def run_campaign(agents: int = 200, workers: int = 8, max_live: int = 5_000) -> dict:
    from rhapsody import Session
    from rhapsody.backends import ConcurrentExecutionBackend

    url = start_mcp_server()
    requests, replies, service_thread, service = start_tool_service(url, processes=1)
    client = ToolServiceClient(requests, replies[0], index=0)
    client.start()
    session = Session(backends=[await ConcurrentExecutionBackend(ThreadPoolExecutor(workers))])
    runtime = ToolRuntime(session, client)
    tools = bind_tools(
        runtime,
        [
            task_tool(simulate, suspend=True),
            mcp_tool("lookup", description="Reference data for a candidate.", args_schema=LookupArgs),
            note,
        ],
    )
    fleet = Fleet([ScriptedReplica(), ScriptedReplica()], admission=TokenAdmission(tokens=20_000, requests=32))
    saver = KeyValueSaver(MemoryStore())
    answers: dict[str, str] = {}
    host = AgentHost(
        GraphRunner(build_graph(fleet, tools, saver)),
        max_live_agents=max_live,
        stagger=Stagger(batch=100, interval_s=0.05),
        on_done=lambda result, out: answers.__setitem__(result.id, out["messages"][-1].content if out else None),
    )
    work = [Agent(f"agent-{i:05d}", {"messages": [HumanMessage(f"Find the best temperature for cand-{i:05d}")]}) for i in range(agents)]
    try:
        report = await host.run(work)
    finally:
        client.stop()
        requests.put(pickle.dumps(None))
        service_thread.join(10)
        await session.close()
    return {
        "agents": agents,
        "finished": report.finished,
        "suspensions": sum(a.suspensions for a in report.agents),
        "answers": sorted(set(answers.values())),
        "wall_s": report.wall_s,
        "states": report.states,
        "loop_lag": report.loop_lag,
        "collector": report.collector,
        "fleet": fleet.stats(),
        "tools": runtime.stats(),
        "checkpoints": saver.stats(),
        "tool_service": service.get("stats"),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--agents", type=int, default=200)
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()
    summary = asyncio.run(run_campaign(a.agents, a.workers))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
