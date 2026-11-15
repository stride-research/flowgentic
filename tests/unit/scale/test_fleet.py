"""The fleet: routing, admission, accounting, recovery, and its chat model."""

import asyncio
import subprocess
import sys

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import START, MessagesState, StateGraph

from flowgentic import _agents
from flowgentic.fleet import (
    TRANSIENT,
    AdmissionGate,
    AgentAffinity,
    Fleet,
    LeastLoaded,
    Recovery,
    ReplicaLoad,
    RequestAdmission,
    RoundRobin,
    TokenAdmission,
)
from flowgentic.host import AgentState, AgentStates


class Slow:
    """A chat model that records how many calls it serves at once, and the
    agents it served."""

    def __init__(self, reply=None, usage=None):
        self.now = self.most = 0
        self.reply = reply or AIMessage("DONE")
        self.usage = usage
        self.seen = []

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.now += 1
        self.most = max(self.most, self.now)
        await asyncio.sleep(0.01)
        self.now -= 1
        reply = self.reply.model_copy()
        if self.usage:
            reply.usage_metadata = self.usage
        return reply


def agents(n):
    return [f"mat-{i:06d}" for i in range(n)]


def test_affinity_is_the_same_in_another_process():
    loads = [ReplicaLoad(0, 0, 0)] * 4
    here = [AgentAffinity().choose(a, loads) for a in agents(50)]
    code = (
        "from flowgentic.fleet import AgentAffinity, ReplicaLoad;"
        "print([AgentAffinity().choose(f'mat-{i:06d}', [ReplicaLoad(0,0,0)]*4) for i in range(50)])"
    )
    there = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert str(here) == there.stdout.strip()


def test_affinity_spreads_agents():
    counts = [0] * 4
    for a in agents(4000):
        counts[AgentAffinity().choose(a, [ReplicaLoad(0, 0, 0)] * 4)] += 1
    assert min(counts) > 850


def test_baseline_routings():
    rr = RoundRobin()
    assert [rr.choose("a", [ReplicaLoad(0, 0, 0)] * 3) for _ in range(4)] == [0, 1, 2, 0]
    loads = [ReplicaLoad(10, 3, 1), ReplicaLoad(99, 1, 0), ReplicaLoad(5, 3, 1)]
    assert LeastLoaded().choose("a", loads) == 1


async def test_admission_is_first_come_and_cancellation_releases():
    gate = AdmissionGate(tokens=100, requests=10)
    await gate.acquire(80)
    big = asyncio.create_task(gate.acquire(50))  # does not fit: waits
    small = asyncio.create_task(gate.acquire(10))  # fits, but is behind big
    await asyncio.sleep(0.01)
    assert gate.load() == ReplicaLoad(80, 1, 2)
    big.cancel()
    await asyncio.sleep(0.01)
    assert small.done() and gate.load() == ReplicaLoad(90, 2, 0)
    gate.release(80)
    gate.release(10)
    await gate.acquire(500)  # larger than the cap: admitted alone
    assert gate.load() == ReplicaLoad(500, 1, 0)


def test_share_scales_the_caps():
    gate = TokenAdmission(tokens=1000, requests=10).gate(share=0.25)
    assert (gate.cap_tokens, gate.cap_requests) == (250, 2)
    assert RequestAdmission(3).gate(share=0.01).cap_requests == 1


async def test_admission_cap_states_and_records():
    models = [Slow(usage={"input_tokens": 100, "output_tokens": 5, "input_token_details": {"cache_read": 60}}), Slow()]
    names = agents(40)
    states = AgentStates(names)
    fleet = Fleet(models, admission=RequestAdmission(3))
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    seen_states = set()

    async def one(agent):
        _agents.set_current_agent(agent, states)
        states.enter(agent, AgentState.WORKING)
        task = asyncio.create_task(fleet.ainvoke(agent, [HumanMessage("hi"), ToolMessage("r", tool_call_id="c", name="simulate")]))
        await asyncio.sleep(0.005)
        seen_states.add(states.state(agent))
        await task

    await asyncio.gather(*(asyncio.create_task(one(a)) for a in names))
    assert all(m.most <= 3 for m in models)
    stats = fleet.stats()
    assert stats["calls"] == 40 and stats["replies_final"] == 40
    assert {AgentState.MODEL_QUEUED, AgentState.MODEL_REQUEST} <= seen_states
    assert states.counts()[AgentState.WORKING] == 40
    assert all(r.after_tool == "simulate" and r.gap_s is None for r in records)
    with_usage = [r for r in records if r.prompt_tokens]
    assert with_usage and all(r.cached_tokens == 60 for r in with_usage)
    assert stats["cached_tokens"] == 60 * len(with_usage)


async def test_the_estimate_learns_and_tracks_a_bounded_number_of_agents():
    model = Slow(usage={"input_tokens": 1000, "output_tokens": 10})
    fleet = Fleet([model], admission=TokenAdmission(10_000, 10), tracked_agents=3)
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    for a in agents(5):
        await fleet.ainvoke(a, [HumanMessage("x" * 400)])
    await fleet.ainvoke("mat-000004", [HumanMessage("x" * 400), AIMessage("DONE"), HumanMessage("y" * 100)])
    assert len(fleet._core.agents) == 3
    second = records[-1]
    assert second.prev_prompt_tokens == 1000 and second.gap_s is not None
    assert second.estimate >= 1010  # the first call's size, as the server counted it


class APIConnectionError(Exception):
    """Named as the OpenAI client's: transient() goes by the class names."""


class Down:
    def __init__(self, server, tag="v1"):
        self.server, self.tag, self.calls = server, tag, 0

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        await asyncio.sleep(0.001)
        if not self.server["up"] or self.server.get("tag", self.tag) != self.tag:
            raise APIConnectionError("Connection error.")
        return AIMessage(f"DONE {self.tag}")


async def test_calls_wait_for_a_server_that_comes_back():
    server = {"up": True}
    model = Down(server)

    async def check(i):
        return model if server["up"] else None

    fleet = Fleet([model], admission=RequestAdmission(4), recovery=Recovery(check, poll_s=0.02))
    server["up"] = False
    calls = asyncio.gather(*(fleet.ainvoke(a, [HumanMessage("hi")]) for a in agents(20)))
    await asyncio.sleep(0.2)
    server["up"] = True
    replies = await calls
    assert {r.content for r in replies} == {"DONE v1"}
    stats = fleet.stats()
    assert stats["retried_calls"] > 0 and stats["recovery"]["outages"] == {0: 1}


async def test_a_server_back_with_a_new_endpoint_gets_a_new_model():
    server = {"up": True, "tag": "v1"}
    old = Down(server, "v1")

    async def check(i):
        return Down(server, "v2") if server.get("tag") == "v2" else None

    fleet = Fleet([old], recovery=Recovery(check, poll_s=0.02))
    server["tag"] = "v2"  # requeued: a new port and key
    reply = await fleet.ainvoke("a", [HumanMessage("hi")])
    assert reply.content == "DONE v2" and fleet._core.generation == [1]


async def test_never_back_fails_with_the_original_error_and_bad_requests_fail_at_once():
    server = {"up": False}

    async def check(i):
        return None

    fleet = Fleet([Down(server)], recovery=Recovery(check, outage_s=0.1, poll_s=0.02))
    with pytest.raises(APIConnectionError):
        await fleet.ainvoke("a", [HumanMessage("hi")])
    assert fleet.stats()["recovery"]["gave_up"] == {0: 1}

    class Bad(Down):
        async def ainvoke(self, messages):
            raise ValueError("400 bad request")

    fleet = Fleet([Bad(server)], recovery=Recovery(check, outage_s=10, poll_s=0.02))
    with pytest.raises(ValueError):
        await fleet.ainvoke("a", [HumanMessage("hi")])  # fails at once: not transient


def test_which_errors_of_langchain_openai_are_transient():
    import openai
    from langchain_openai.chat_models import base

    for name in TRANSIENT:
        assert issubclass(getattr(openai, name), openai.OpenAIError)

    def matches(cls):
        return any(c.__name__ in TRANSIENT for c in cls.__mro__)

    for name in ("OpenAIConnectionError", "OpenAITimeoutError", "OpenAIAPIError", "OpenAIAuthenticationError"):
        assert matches(getattr(base, name)), name
    for name in ("OpenAIInvalidRequestError", "OpenAIContextOverflowError", "OpenAIModelNotFoundError"):
        assert not matches(getattr(base, name)), name


async def test_the_chat_model_routes_by_the_thread_id():
    model = Slow()
    fleet = Fleet([model, Slow(), Slow()])
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    chat = fleet.chat_model().bind_tools([])

    async def node(state: MessagesState):
        return {"messages": [await chat.ainvoke(state["messages"])]}  # no config passed

    g = StateGraph(MessagesState)
    g.add_node("model", node)
    g.add_edge(START, "model")
    graph = g.compile()
    for thread in ("t-1", "t-2", "t-1"):
        await graph.ainvoke({"messages": [HumanMessage("hi")]}, {"configurable": {"thread_id": thread}})
    assert [r.agent_id for r in records] == ["t-1", "t-2", "t-1"]
    assert records[0].replica == records[2].replica
    with pytest.raises(ValueError, match="routes by agent"):
        await fleet.chat_model().ainvoke([HumanMessage("hi")])
