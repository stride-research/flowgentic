"""The fleet: routing, admission, accounting, recovery, its chat model and its
metrics."""

import asyncio
import inspect
import itertools
import json
import subprocess
import sys
import warnings

import httpx
import pytest
from langchain_core.callbacks import AsyncCallbackHandler, get_usage_metadata_callback
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
    GenericFakeChatModel,
)
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.graph import START, MessagesState, StateGraph
from pydantic import BaseModel

import flowgentic.fleet as fleet_module
from flowgentic import _agents
from flowgentic.fleet import (
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

    async def ainvoke(self, messages, config=None):
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


def one_node(node):
    """A graph of one node."""
    g = StateGraph(MessagesState)
    g.add_node("model", node)
    g.add_edge(START, "model")
    return g.compile()


def thread(thread_id):
    return {"configurable": {"thread_id": thread_id}}


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
    await gate.acquire(80, "a")
    big = asyncio.create_task(gate.acquire(50, "b"))  # does not fit: waits
    small = asyncio.create_task(gate.acquire(10, "c"))  # fits, but is behind big
    await asyncio.sleep(0.01)
    assert gate.load() == ReplicaLoad(80, 1, 2)
    big.cancel()
    await asyncio.sleep(0.01)
    assert small.done() and gate.load() == ReplicaLoad(90, 2, 0)
    gate.release(80, "a")
    gate.release(10, "c")
    await gate.acquire(500, "d")  # larger than the cap: admitted alone
    assert gate.load() == ReplicaLoad(500, 1, 0)


async def test_a_request_admitted_then_cancelled_releases_what_it_took():
    gate = AdmissionGate(tokens=100, requests=1)
    await gate.acquire(10, "a")
    waiting = asyncio.create_task(gate.acquire(20, "b"))
    await asyncio.sleep(0)
    gate.release(10, "a")  # admits b, which has not run yet
    waiting.cancel()
    await asyncio.sleep(0.01)
    assert waiting.cancelled() and gate.load() == ReplicaLoad(0, 0, 0)


async def test_a_request_above_the_token_cap_is_counted_and_warned_of_once(caplog):
    gate = TokenAdmission(tokens=1000, requests=10).gate(share=0.25)  # 250 tokens here
    with caplog.at_level("WARNING", logger="flowgentic.fleet"):
        await gate.acquire(100, "a")
        gate.release(100, "a")
        for n in (400, 600):  # each larger than this process's share: admitted alone
            await gate.acquire(n, "b")
            gate.release(n, "b")
    assert gate.stats()["oversize"] == 2
    (warning,) = [r for r in caplog.records if r.name == "flowgentic.fleet"]
    assert "400" in warning.getMessage() and "250" in warning.getMessage()
    assert RequestAdmission(3).gate(share=1.0).stats()["oversize"] == 0


class Hanging(Slow):
    """A server that takes its time, until it is told to hurry."""

    hang = True

    async def ainvoke(self, messages, config=None):
        self.now += 1
        try:
            await asyncio.sleep(30 if self.hang else 0)
        finally:
            self.now -= 1
        return AIMessage("DONE")


async def test_cancelling_a_call_at_its_replica_gives_back_its_place():
    model = Hanging()
    fleet = Fleet([model], admission=TokenAdmission(1000, 1))
    states = AgentStates(["agent-1"])

    async def one():
        _agents.set_current_agent("agent-1", states)
        states.enter("agent-1", AgentState.WORKING)
        await fleet.ainvoke("agent-1", [HumanMessage("x" * 400)])

    call = asyncio.create_task(one())
    await asyncio.sleep(0.02)
    assert model.now == 1 and states.state("agent-1") == AgentState.MODEL_REQUEST
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert fleet.load() == [ReplicaLoad(0, 0, 0)] and fleet._core.in_flight == 0
    assert model.now == 0 and states.state("agent-1") == AgentState.WORKING
    model.hang = False
    await asyncio.wait_for(fleet.ainvoke("agent-2", [HumanMessage("hi")]), 1)  # admitted at once


async def test_cancelling_a_call_that_waits_out_an_outage_gives_back_its_place():
    async def check(i):
        return None

    fleet = Fleet([Down({"up": False})], admission=RequestAdmission(1), recovery=Recovery(check, poll_s=0.02))
    call = asyncio.create_task(fleet.ainvoke("a", [HumanMessage("hi")]))
    await asyncio.sleep(0.05)
    assert fleet._core.recovery._watches  # the call failed and waits
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert fleet.load() == [ReplicaLoad(0, 0, 0)] and fleet._core.in_flight == 0


def test_a_negative_reply_reserve_is_refused():
    with pytest.raises(ValueError, match="reply_reserve"):
        Fleet([Slow()], reply_reserve=-1)
    Fleet([Slow()], reply_reserve=0)


async def test_a_fleet_warns_of_requests_above_the_token_cap_once_for_all_its_replicas(caplog):
    fleet = Fleet(
        [Recording() for _ in range(4)], admission=TokenAdmission(100, 10), routing=RoundRobin(), name="big"
    )
    with caplog.at_level("WARNING", logger="flowgentic.fleet"):
        for i in range(4):  # one call, above the cap, to each replica
            await fleet.ainvoke(f"a{i}", [HumanMessage("x" * 1000)])
    assert [g["oversize"] for g in fleet.stats()["gates"]] == [1, 1, 1, 1]
    (warning,) = [r for r in caplog.records if r.name == "flowgentic.fleet"]
    assert "big" in warning.getMessage()


def test_share_scales_the_caps():
    gate = TokenAdmission(tokens=1000, requests=10).gate(share=0.25)
    assert (gate.cap_tokens, gate.cap_requests) == (250, 2)
    assert RequestAdmission(3).gate(share=0.01).cap_requests == 1


@pytest.mark.parametrize(
    "admission",
    [TokenAdmission(tokens=0, requests=96), TokenAdmission(tokens=1000, requests=0), RequestAdmission(0)],
)
def test_a_cap_below_one_is_refused_not_raised_to_one(admission):
    with pytest.raises(ValueError, match="at least 1"):
        Fleet([Slow()], admission=admission)


# --- the admission seam ------------------------------------------------------------


class Ledger:
    """A custom gate: it admits every call, and records whose calls it saw."""

    def __init__(self, share):
        self.share = share
        self.events = []
        self.requests = 0

    async def acquire(self, n, agent_id):
        self.events.append(("acquire", agent_id))
        self.requests += 1

    def release(self, n, agent_id):
        self.events.append(("release", agent_id))
        self.requests -= 1

    def agent_done(self, agent_id):
        self.events.append(("done", agent_id))

    def load(self):
        return ReplicaLoad(0, self.requests, 0)

    def stats(self):
        return {"policy": "ledger", "events": len(self.events)}


class LedgerAdmission:
    counts_tokens = False

    def __init__(self, make=Ledger):
        self.make = make
        self.gates = []

    def gate(self, share):
        gate = self.make(share)
        self.gates.append(gate)
        return gate


async def test_a_custom_admission_sees_whose_call_it_admits():
    admission = LedgerAdmission()
    fleet = Fleet([Recording(), Recording()], admission=admission, share=0.5)
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    chat = fleet.chat_model()

    async def node(state: MessagesState):
        return {"messages": [await chat.ainvoke(state["messages"])]}

    await fleet.ainvoke("a", [HumanMessage("hi")])
    await one_node(node).ainvoke({"messages": [HumanMessage("hi")]}, thread("t-1"))
    assert [g.share for g in admission.gates] == [0.5, 0.5]
    for record in records:
        gate = admission.gates[record.replica]
        assert gate.events.count(("acquire", record.agent_id)) == 1
        assert gate.events.count(("release", record.agent_id)) == 1
    assert fleet.stats()["gates"] == [g.stats() for g in admission.gates]
    assert fleet.load() == [ReplicaLoad(0, 0, 0)] * 2

    fleet.agent_done("a")  # every gate hears that the agent ended
    assert all(g.events[-1] == ("done", "a") for g in admission.gates)
    await fleet.ainvoke("a", [HumanMessage("hi")])  # and the fleet forgot its size
    assert records[-1].gap_s is None and records[-1].prev_prompt_tokens is None


class CountedLoads(Ledger):
    loads = 0

    def load(self):
        CountedLoads.loads += 1
        return super().load()


@pytest.mark.parametrize(
    "routing, most",
    [(AgentAffinity(), 0), (RoundRobin(), 0), (LeastLoaded(), 8)],  # at most one per replica
)
async def test_a_call_reads_the_replicas_loads_only_if_its_routing_does(routing, most):
    fleet = Fleet([Recording() for _ in range(8)], admission=LedgerAdmission(CountedLoads), routing=routing)
    CountedLoads.loads = 0
    await fleet.ainvoke("a", [HumanMessage("hi")])
    assert CountedLoads.loads == most


def test_the_built_in_gates_are_gates_and_a_gate_that_is_not_is_refused():
    Gate = fleet_module.Gate
    assert isinstance(TokenAdmission(1000, 10).gate(1.0), Gate)
    assert isinstance(RequestAdmission(10).gate(1.0), Gate)

    class NoStats:
        def __init__(self, share):
            pass

        async def acquire(self, n, agent_id):
            pass

        def release(self, n, agent_id):
            pass

        def load(self):
            return ReplicaLoad(0, 0, 0)

    with pytest.raises(TypeError, match="Gate"):
        Fleet([Recording()], admission=LedgerAdmission(NoStats))


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


class Counting:
    """A server that counts a prompt as ``base`` tokens, such as tool schemas
    and a template the messages do not show, plus ``per_char`` per character."""

    def __init__(self, base, per_char=0.25):
        self.base, self.per_char = base, per_char

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, **kwargs):
        chars = sum(len(m.content) for m in messages)
        reply = AIMessage("DONE")
        reply.usage_metadata = {
            "input_tokens": self.base + int(chars * self.per_char),
            "output_tokens": 5,
            "total_tokens": 0,
        }
        return reply


class SomeWithTools(Counting):
    """A server on which the agents whose prompt starts with "t" carry 2,750
    tokens of tool schemas, and the others none."""

    def __init__(self):
        super().__init__(0)

    async def ainvoke(self, messages, **kwargs):
        reply = await super().ainvoke(messages, **kwargs)
        if messages[0].content.startswith("t"):
            reply.usage_metadata["input_tokens"] += 2750
        return reply


@pytest.mark.parametrize("kind, counted", [("p", 25_000), ("t", 27_750)])
async def test_first_calls_with_and_without_tools_do_not_lower_the_estimate(kind, counted):
    # Shorter prompts with tool schemas count more tokens than longer ones without.
    fleet = Fleet([SomeWithTools()], admission=TokenAdmission(235_000, 96))
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    for i in range(100):
        await fleet.ainvoke(f"t{i}", [HumanMessage("t" * 1_000)])  # 3,000 tokens
        await fleet.ainvoke(f"p{i}", [HumanMessage("p" * 3_000)])  # 750 tokens
    await fleet.ainvoke("long", [HumanMessage(kind * 100_000)])
    estimate = records[-1].estimate - fleet.stats()["reply_reserve_tokens"]
    assert records[-1].prompt_tokens == counted
    assert 100_000 * fleet.stats()["tokens_per_char"]["added"] <= estimate  # at least its text
    assert 0.9 * counted <= estimate <= 1.5 * counted


async def first_call_estimate(server, prompts, long):
    """The estimate of a first call of ``long`` characters, after first calls
    of the given lengths; and what the server counted."""
    fleet = Fleet([server], admission=TokenAdmission(235_000, 96))
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    for i, n in enumerate(prompts):
        await fleet.ainvoke(f"a{i}", [HumanMessage("x" * n)])
    await fleet.ainvoke("long", [HumanMessage("x" * long)])
    return records[-1].estimate - fleet.stats()["reply_reserve_tokens"], records[-1].prompt_tokens, fleet


async def test_a_long_first_prompt_after_short_ones_is_not_overestimated():
    # Short first prompts that the server counts as mostly fixed tokens.
    estimate, counted, fleet = await first_call_estimate(Counting(1000), [41] * 200, 40_000)
    assert counted == 11_000
    assert 0.5 * counted <= estimate <= 1.5 * counted
    per_char = fleet.stats()["tokens_per_char"]
    # The fixed part, less what the floor of 0.3 tokens per character, not yet
    # learned from later calls, takes from it.
    assert 850 <= per_char["first_call_base"] <= 1_100


async def test_the_first_call_estimate_learns_a_fixed_part_and_a_ratio():
    lengths = [100 + 37 * (i % 50) * (i % 7) for i in range(300)]  # 100 to 11,000 characters
    estimate, counted, fleet = await first_call_estimate(Counting(2000, 0.5), lengths, 40_000)
    assert counted == 22_000
    assert 0.9 * counted <= estimate <= 1.1 * counted
    per_char = fleet.stats()["tokens_per_char"]
    assert abs(per_char["first_call"] - 0.5) < 0.05
    assert abs(per_char["first_call_base"] - 2000) < 200


async def test_the_first_call_estimate_starts_from_a_ratio_alone():
    fleet = Fleet([Counting(0)], admission=TokenAdmission(235_000, 96))
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    assert fleet.stats()["tokens_per_char"]["first_call_base"] is None
    await fleet.ainvoke("a", [HumanMessage("x" * 1000)])
    assert records[0].estimate == int(1000 * 0.45) + 64  # nothing learned yet
    assert fleet.stats()["tokens_per_char"]["first_call_base"] is not None


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
    for _ in range(100):  # the call's own outage_s ends first; the watch's at its next poll
        if fleet.stats()["recovery"]["gave_up"]:
            break
        await asyncio.sleep(0.01)
    assert fleet.stats()["recovery"]["gave_up"] == {0: 1}

    class Bad(Down):
        async def ainvoke(self, messages):
            raise ValueError("400 bad request")

    fleet = Fleet([Bad(server)], recovery=Recovery(check, outage_s=10, poll_s=0.02))
    with pytest.raises(ValueError):
        await fleet.ainvoke("a", [HumanMessage("hi")])  # fails at once: not transient


class InternalServerError(Exception):
    """Named as the OpenAI client's error for a 5xx."""


async def test_a_call_the_replica_keeps_refusing_fails_after_outage_s():
    class Refusing(Down):
        async def ainvoke(self, messages):
            self.calls += 1
            await asyncio.sleep(0.001)
            raise InternalServerError("500")

    model = Refusing({"up": True})

    async def check(i):
        return model  # its health check passes, but every call fails

    fleet = Fleet([model], recovery=Recovery(check, outage_s=0.3, poll_s=0.05))
    with pytest.raises(InternalServerError):
        await asyncio.wait_for(fleet.ainvoke("a", [HumanMessage("hi")]), 5)
    assert model.calls < 10  # a pause between tries, not a loop of them


async def test_a_call_fails_after_outage_s_while_the_check_hangs():
    answer = asyncio.Event()

    async def check(i):
        await answer.wait()  # a health probe to a node that does not answer

    fleet = Fleet([Down({"up": False})], recovery=Recovery(check, outage_s=0.1, poll_s=0.02))
    try:
        with pytest.raises(APIConnectionError):
            await asyncio.wait_for(fleet.ainvoke("a", [HumanMessage("hi")]), 5)
    finally:
        answer.set()


async def test_a_check_that_hangs_once_does_not_stop_recovery():
    server = {"up": True, "tag": "v2"}  # already back, elsewhere
    new = Down(server, "v2")
    probes = []

    async def check(i):
        probes.append(i)
        if len(probes) == 1:
            await asyncio.Event().wait()  # a probe to the old node, which never answers
        return new

    recovery = Recovery(check, outage_s=5, poll_s=0.02, check_timeout_s=0.05)
    fleet = Fleet([Down(server, "v1")], recovery=recovery)
    reply = await asyncio.wait_for(fleet.ainvoke("a", [HumanMessage("hi")]), 5)
    assert reply.content == "DONE v2" and len(probes) == 2
    assert recovery.stats()["check_failures"] == {0: 1}


async def test_a_call_that_joins_a_watch_near_its_end_waits_out_its_own_outage():
    server = {"up": False}
    model = Down(server)

    async def check(i):
        return model if server["up"] else None

    fleet = Fleet([model], recovery=Recovery(check, outage_s=1.0, poll_s=0.02))
    first = asyncio.create_task(fleet.ainvoke("a", [HumanMessage("hi")]))
    await asyncio.sleep(0.7)
    late = asyncio.create_task(fleet.ainvoke("b", [HumanMessage("hi")]))  # fails at 0.7
    await asyncio.sleep(0.6)  # the first call's outage_s has passed
    assert first.done() and isinstance(first.exception(), APIConnectionError)
    assert not late.done()  # its own outage_s has not
    server["up"] = True
    assert (await asyncio.wait_for(late, 5)).content == "DONE v1"
    assert fleet.stats()["recovery"]["gave_up"] == {0: 1}


async def test_a_call_with_less_than_poll_s_left_starts_no_new_watch():
    loop = asyncio.get_running_loop()
    checks = []

    async def check(i):
        checks.append(loop.time())
        return None

    recovery = Recovery(check, outage_s=0.3, poll_s=0.1)
    fleet = Fleet([Down({"up": False})], recovery=recovery)
    first = asyncio.create_task(fleet.ainvoke("a", [HumanMessage("hi")]))
    await asyncio.sleep(0.05)
    late = asyncio.create_task(fleet.ainvoke("b", [HumanMessage("hi")]))  # 0.05 s behind
    for call in (first, late):
        with pytest.raises(APIConnectionError):
            await asyncio.wait_for(call, 5)
    last = loop.time()
    await asyncio.sleep(0.25)
    assert [t for t in checks if t > last] == []  # no check that no call waits for
    assert recovery.stats()["gave_up"] == {0: 1} and not recovery._tasks


async def test_a_call_that_joins_a_watch_whose_check_hangs_gets_a_new_watch():
    server = {"up": True, "tag": "v2"}  # already back, elsewhere
    new = Down(server, "v2")
    probes = []

    async def check(i):
        probes.append(i)
        if len(probes) == 1:
            await asyncio.Event().wait()  # a probe to the old node, which never answers
        return new

    # The first probe outlives the first call's outage_s.
    fleet = Fleet([Down(server, "v1")], recovery=Recovery(check, outage_s=0.3, poll_s=0.02))
    with pytest.raises(APIConnectionError):
        await asyncio.wait_for(fleet.ainvoke("a", [HumanMessage("hi")]), 5)
    reply = await asyncio.wait_for(fleet.ainvoke("b", [HumanMessage("hi")]), 5)
    assert reply.content == "DONE v2" and len(probes) == 2


class SlowDown(Down):
    """A server that takes 50 ms to answer, or to fail while it is down."""

    async def ainvoke(self, messages):
        await asyncio.sleep(0.05)
        return await super().ainvoke(messages)


async def test_a_records_wait_is_for_admission_and_its_outage_is_apart():
    server = {"up": False}
    model = SlowDown(server)

    async def check(i):
        return model if server["up"] else None

    fleet = Fleet([model], recovery=Recovery(check, poll_s=0.02))  # no admission
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))

    async def back():
        await asyncio.sleep(0.4)
        server["up"] = True

    asyncio.create_task(back())
    t = asyncio.get_running_loop().time()
    await fleet.ainvoke("a", [HumanMessage("hi")])
    total = asyncio.get_running_loop().time() - t
    (record,) = records
    assert record.wait_s < 0.02  # nothing to wait for: no admission
    assert 0.04 <= record.service_s < 0.1  # the try that succeeded
    assert record.recovery_s >= 0.35  # the failed try and the outage
    assert abs(record.wait_s + record.service_s + record.recovery_s - total) < 0.02

    await fleet.ainvoke("a", [HumanMessage("hi")])
    assert records[-1].recovery_s == 0.0


async def test_a_check_that_raises_counts_as_down():
    server = {"up": False}
    model = Down(server)

    async def check(i):
        if not server["up"]:
            raise ConnectionRefusedError("the health probe was refused")
        return model

    recovery = Recovery(check, outage_s=5, poll_s=0.02)
    fleet = Fleet([model], recovery=recovery)
    call = asyncio.create_task(fleet.ainvoke("a", [HumanMessage("hi")]))
    await asyncio.sleep(0.1)
    server["up"] = True
    assert (await asyncio.wait_for(call, 5)).content == "DONE v1"
    assert recovery.stats()["check_failures"][0] >= 2

    async def refused(i):
        raise ConnectionRefusedError("the health probe was refused")

    fleet = Fleet([Down({"up": False})], recovery=Recovery(refused, outage_s=0.1, poll_s=0.02))
    with pytest.raises(APIConnectionError):  # the call's own error, not the check's
        await asyncio.wait_for(fleet.ainvoke("a", [HumanMessage("hi")]), 5)


async def test_the_watch_runs_outside_the_callers_context():
    server = {"up": False}
    model = Down(server)
    seen = set()

    async def check(i):
        seen.add(_agents.current_agent())
        return model if server["up"] else None

    fleet = Fleet([model], recovery=Recovery(check, poll_s=0.02))
    states = AgentStates(["agent-1", "agent-2"])

    async def one(agent):
        _agents.set_current_agent(agent, states)
        await fleet.ainvoke(agent, [HumanMessage("hi")])

    calls = [asyncio.create_task(one("agent-1"))]
    await asyncio.sleep(0.05)
    calls.append(asyncio.create_task(one("agent-2")))
    await asyncio.sleep(0.1)
    server["up"] = True
    await asyncio.wait_for(asyncio.gather(*calls), 5)
    assert seen == {None}


@pytest.mark.parametrize("bad", [{"outage_s": -1}, {"poll_s": 0}, {"check_timeout_s": 0}])
def test_recovery_refuses_a_negative_outage_and_zero_intervals(bad):
    async def check(i):
        return None

    with pytest.raises(ValueError):
        Recovery(check, **bad)


def test_which_errors_of_langchain_openai_are_transient():
    import openai
    from langchain_openai.chat_models import base

    TRANSIENT = fleet_module._TRANSIENT
    for name in TRANSIENT:
        assert issubclass(getattr(openai, name), openai.OpenAIError)

    def matches(cls):
        return any(c.__name__ in TRANSIENT for c in cls.__mro__)

    # langchain-openai wraps the client's errors in later 1.x releases only
    def wrappers(*names):
        return [getattr(base, n) for n in names if hasattr(base, n)]

    for cls in wrappers("OpenAIConnectionError", "OpenAITimeoutError", "OpenAIAPIError", "OpenAIAuthenticationError"):
        assert matches(cls), cls
    for cls in wrappers("OpenAIInvalidRequestError", "OpenAIContextOverflowError", "OpenAIModelNotFoundError"):
        assert not matches(cls), cls


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


# --- per-call settings, a per-call reserve, and thinking tokens ---------------------


class Recording:
    """A chat model that records each call's keyword arguments, and replies
    with the usage it was given."""

    def __init__(self, usage=None, server=None, tag="v1"):
        self.usage = usage
        self.server = server
        self.tag = tag
        self.kwargs = []
        self.configs = []

    def bind_tools(self, tools, **kwargs):
        return self

    async def ainvoke(self, messages, config=None, **kwargs):
        self.kwargs.append(kwargs)
        self.configs.append(config)
        if self.server is not None and self.server.get("tag", self.tag) != self.tag:
            raise APIConnectionError("Connection error.")
        reply = AIMessage(f"DONE {self.tag}")
        if self.usage:
            reply.usage_metadata = dict(self.usage)
        return reply


THINK = {"extra_body": {"thinking_token_budget": 256}, "max_tokens": 512}


async def test_a_calls_settings_reach_its_replica_and_only_for_that_call():
    model = Recording()
    fleet = Fleet([model]).bind_tools([])
    await fleet.ainvoke("a", [HumanMessage("hi")], **THINK)
    await fleet.ainvoke("a", [HumanMessage("hi")])
    assert model.kwargs == [THINK, {}]


async def test_a_calls_settings_survive_a_replica_rebuilt_by_recovery():
    server = {"tag": "v1"}
    old, new = Recording(server=server, tag="v1"), Recording(server=server, tag="v2")

    async def check(i):
        return new if server["tag"] == "v2" else None

    fleet = Fleet([old], recovery=Recovery(check, poll_s=0.02)).bind_tools([])
    server["tag"] = "v2"  # the server came back elsewhere
    reply = await fleet.ainvoke("a", [HumanMessage("hi")], **THINK)
    assert reply.content == "DONE v2"
    assert old.kwargs == [THINK] and new.kwargs == [THINK]


async def test_a_reserve_adds_to_the_admission_estimate():
    fleet = Fleet([Recording()], admission=TokenAdmission(100_000, 10))
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    await fleet.ainvoke("a", [HumanMessage("x" * 400)])
    await fleet.ainvoke("b", [HumanMessage("x" * 400)], reserve=256)
    assert records[1].estimate == records[0].estimate + 256


async def two_calls(usage):
    """The records of an agent's first two calls, on a replica that reports
    ``usage``."""
    fleet = Fleet([Recording(usage=usage)])
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    first = [HumanMessage("x" * 400)]
    reply = await fleet.ainvoke("a", first)
    await fleet.ainvoke("a", [*first, reply, HumanMessage("y" * 100)])
    return records


async def test_thinking_tokens_are_recorded_and_left_out_of_the_agents_size():
    plain = await two_calls({"input_tokens": 1000, "output_tokens": 300})
    thought = await two_calls(
        {"input_tokens": 1000, "output_tokens": 300, "output_token_details": {"reasoning": 200}}
    )
    assert plain[0].reasoning_tokens is None
    assert thought[0].reasoning_tokens == 200
    # The reply's thinking is not in the conversation, so not in the next prompt.
    assert thought[1].estimate == plain[1].estimate - 200


async def test_a_record_carries_the_callers_meta():
    fleet = Fleet([Recording()])
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    await fleet.ainvoke("a", [HumanMessage("hi")], meta={"budget": 256})
    await fleet.ainvoke("a", [HumanMessage("hi")])
    assert [r.meta for r in records] == [{"budget": 256}, None]


async def test_the_chat_model_passes_settings_reserve_and_meta_per_call():
    model = Recording()
    fleet = Fleet([model], admission=TokenAdmission(100_000, 10))
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    chat = fleet.chat_model().bind_tools([])

    async def node(state: MessagesState):
        reply = await chat.ainvoke(state["messages"], reserve=256, meta={"budget": 256}, **THINK)
        return {"messages": [reply]}

    g = StateGraph(MessagesState)
    g.add_node("model", node)
    g.add_edge(START, "model")
    await g.compile().ainvoke({"messages": [HumanMessage("hi")]}, {"configurable": {"thread_id": "t-1"}})
    await fleet.ainvoke("t-2", [HumanMessage("hi")])
    assert model.kwargs == [THINK, {}]
    assert records[0].meta == {"budget": 256}
    assert records[0].estimate == records[1].estimate + 256


# --- the chat model ----------------------------------------------------------------


async def test_the_chat_model_takes_the_agent_from_the_config_it_is_given():
    fleet = Fleet([Recording() for _ in range(4)])
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    chat = fleet.chat_model()
    # outside a graph, with settings for the fleet
    await chat.ainvoke([HumanMessage("hi")], config=thread("alone"), reserve=64, meta="alone")

    async def supervisor(state: MessagesState):
        for sub in ("sub-a", "sub-b"):  # two sub-agents, each its own thread
            await chat.ainvoke(state["messages"], config=thread(sub))
        return {}

    await one_node(supervisor).ainvoke({"messages": [HumanMessage("hi")]}, thread("outer"))
    assert [r.agent_id for r in records] == ["alone", "sub-a", "sub-b"]
    assert records[0].meta == "alone"


class ModelStarts(AsyncCallbackHandler):
    """The chat-model runs a call opens: their names and tags."""

    def __init__(self):
        self.runs = []

    async def on_chat_model_start(self, serialized, messages, **kwargs):
        self.runs.append(((serialized or {}).get("name") or kwargs.get("name"), kwargs.get("tags")))


def fake_replica():
    usage = {"input_tokens": 100, "output_tokens": 7, "total_tokens": 107}
    reply = AIMessage("DONE", usage_metadata=usage, response_metadata={"model_name": "m"})
    return FakeMessagesListChatModel(responses=[reply] * 4)


async def test_the_chat_model_opens_no_model_run_of_its_own():
    chat = Fleet([fake_replica()]).chat_model()
    starts = ModelStarts()

    async def node(state: MessagesState):
        return {"messages": [await chat.ainvoke(state["messages"])]}

    with get_usage_metadata_callback() as usage:
        await one_node(node).ainvoke(
            {"messages": [HumanMessage("hi")]}, {**thread("t-1"), "callbacks": [starts]}
        )
    assert usage.usage_metadata["m"]["input_tokens"] == 100  # counted once
    assert [name for name, _ in starts.runs] == ["FakeMessagesListChatModel"]
    # the caller's config reaches the replica: its callbacks and tags
    starts.runs.clear()
    config = {**thread("t-2"), "callbacks": [starts], "tags": ["mine"]}
    await chat.ainvoke([HumanMessage("hi")], config=config)
    ((name, tags),) = starts.runs
    assert name == "FakeMessagesListChatModel" and "mine" in tags


async def test_the_chat_model_streams_the_replicas_tokens_in_a_graph():
    fleet = Fleet([GenericFakeChatModel(messages=itertools.repeat(AIMessage("DONE now")))])
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    chat = fleet.chat_model()

    async def node(state: MessagesState):
        return {"messages": [await chat.ainvoke(state["messages"])]}

    chunks = [
        message
        async for message, _ in one_node(node).astream(
            {"messages": [HumanMessage("hi")]}, thread("t-1"), stream_mode="messages"
        )
    ]
    assert len(chunks) > 1 and all(isinstance(c, AIMessageChunk) for c in chunks)
    assert "".join(c.content for c in chunks) == "DONE now"
    assert [r.agent_id for r in records] == ["t-1"]


def scripted_openai(replies):
    """A ChatOpenAI whose endpoint answers with ``replies``' messages in turn."""
    script = itertools.cycle(replies)

    def answer(request):
        return httpx.Response(
            200,
            json={
                "id": "gen-1",
                "object": "chat.completion",
                "model": "m",
                "choices": [{"index": 0, "message": next(script), "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 7, "total_tokens": 107},
            },
        )

    return ChatOpenAI(
        model="m",
        api_key="x",
        base_url="http://scripted.invalid/v1",
        http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(answer)),
    )


def tool_call(name, args):
    function = {"name": name, "arguments": json.dumps(args)}
    return {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": function}]}


class Answer(BaseModel):
    x: int


async def test_the_chat_model_gives_structured_output():
    fleet = Fleet([scripted_openai([tool_call("Answer", {"x": 3})])])
    records = []
    fleet.add_listener(lambda record, reply: records.append(record))
    structured = fleet.chat_model().with_structured_output(Answer)
    assert await structured.ainvoke([HumanMessage("hi")], config=thread("t")) == Answer(x=3)
    assert [r.agent_id for r in records] == ["t"]


@tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


async def test_prebuilt_agents_send_each_call_to_the_agents_replica():
    from langchain.agents import create_agent
    from langgraph.prebuilt import create_react_agent

    for make in (create_agent, create_react_agent):
        replies = [tool_call("add", {"a": 1, "b": 2}), {"role": "assistant", "content": "3"}]
        fleet = Fleet([scripted_openai(replies), scripted_openai(replies)])
        records = []
        fleet.add_listener(lambda record, reply: records.append(record))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)  # create_react_agent
            agent = make(fleet.chat_model(), [add])
        out = await agent.ainvoke({"messages": [HumanMessage("1+2?")]}, thread("t-7"))
        assert out["messages"][-1].content == "3"
        assert [r.agent_id for r in records] == ["t-7", "t-7"]
        assert records[0].replica == records[1].replica


def test_the_chat_model_is_asynchronous_only():
    with pytest.raises(NotImplementedError, match="ainvoke"):
        Fleet([Recording()]).chat_model().invoke([HumanMessage("hi")], config=thread("t"))


# --- the module's names ------------------------------------------------------------


def test_the_fleet_module_names_only_its_api():
    for name in ("stable_hash", "transient", "TRANSIENT", "AgentState", "message_chars"):
        assert not hasattr(fleet_module, name), name
    defined_here = {
        name
        for name, value in vars(fleet_module).items()
        if not name.startswith("_")
        and not inspect.ismodule(value)
        and getattr(value, "__module__", fleet_module.__name__) == fleet_module.__name__
    }
    assert defined_here == set(fleet_module.__all__)
    assert "Gate" in fleet_module.__all__


# --- metrics -----------------------------------------------------------------------


def points(reader, name: str, fleet: str) -> list:
    """The data points of metric ``name`` for fleet ``fleet``, in the reader's
    latest collection."""
    data = reader.get_metrics_data()
    out = []
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name == name:
                    out.extend(
                        p
                        for p in metric.data.data_points
                        if p.attributes.get("flowgentic.fleet.name") == fleet
                    )
    return out


async def test_the_fleet_records_admission_and_each_replicas_load(metric_reader):
    model = Slow()
    other = Fleet([Slow()], name="other")  # another fleet in the process
    fleet = Fleet([model], admission=RequestAdmission(1), name="metrics")
    calls = asyncio.gather(*(fleet.ainvoke(a, [HumanMessage("hi")]) for a in agents(3)))
    await asyncio.sleep(0.005)
    active = points(metric_reader, "flowgentic.fleet.requests.active", "metrics")
    waiting = points(metric_reader, "flowgentic.fleet.requests.waiting", "metrics")
    assert [(p.attributes["flowgentic.fleet.replica"], p.value) for p in active] == [(0, 1)]
    assert [p.value for p in waiting] == [2]
    await calls
    await other.ainvoke("a", [HumanMessage("hi")])
    (wait,) = points(metric_reader, "flowgentic.fleet.admission.wait", "metrics")
    (service,) = points(metric_reader, "flowgentic.fleet.request.duration", "metrics")
    assert wait.count == 3 and wait.max > 0
    assert service.count == 3 and service.min > 0


async def test_every_try_is_recorded_and_a_failed_one_with_its_error(metric_reader):
    server = {"up": False}
    model = Down(server)

    async def check(i):
        server["up"] = True  # back at the first check
        return model

    fleet = Fleet([model], admission=RequestAdmission(4), recovery=Recovery(check, poll_s=0.02), name="tries")
    await fleet.ainvoke("a", [HumanMessage("hi")])  # fails once, then succeeds

    class Bad(Down):
        async def ainvoke(self, messages):
            raise ValueError("400 bad request")

    failing = Fleet([Bad(server)], name="refused")
    with pytest.raises(ValueError):
        await failing.ainvoke("a", [HumanMessage("hi")])

    (wait,) = points(metric_reader, "flowgentic.fleet.admission.wait", "tries")
    assert wait.count == 2  # one admission per try
    service = {
        p.attributes.get("error.type"): p.count
        for p in points(metric_reader, "flowgentic.fleet.request.duration", "tries")
    }
    assert service == {"APIConnectionError": 1, None: 1}
    (wait,) = points(metric_reader, "flowgentic.fleet.admission.wait", "refused")
    (service,) = points(metric_reader, "flowgentic.fleet.request.duration", "refused")
    assert wait.count == 1 and service.count == 1
    assert service.attributes["error.type"] == "ValueError"


# --- spans: last, as the instrumentation stays on for the rest of the module ------


@pytest.mark.usefixtures("genai_instrumentation")
async def test_one_call_through_the_chat_model_is_one_inference_span(span_exporter):
    chat = Fleet([scripted_openai([{"role": "assistant", "content": "DONE"}])]).chat_model()

    async def node(state: MessagesState):
        return {"messages": [await chat.ainvoke(state["messages"])]}

    await one_node(node).ainvoke({"messages": [HumanMessage("hi")]}, thread("t-1"))
    (span,) = [s for s in span_exporter.get_finished_spans() if s.attributes.get("gen_ai.operation.name") == "chat"]
    assert span.attributes["gen_ai.usage.input_tokens"] == 100
    assert span.attributes["server.address"] == "scripted.invalid"
