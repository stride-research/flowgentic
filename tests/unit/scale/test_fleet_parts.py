"""Exact admission caps per process (``Fleet(process=...)``), and processes
that send to a slice of the replicas (``pack=True``), with fake model
servers that count the requests they hold at once."""

import asyncio
import math

import pytest

import flowgentic.fleet as fleet_module
from flowgentic.fleet import (
    AgentAffinity,
    Fleet,
    ReplicaLoad,
    Reply,
    RequestAdmission,
    Share,
    TokenAdmission,
    Usage,
)

TOKENS, REQUESTS = 235_000, 256


class Server:
    """A fake model server: the requests it holds now and at most, and the
    processes that sent it any."""

    def __init__(self, latency_s=0.005):
        self.latency_s = latency_s
        self.now = self.most = self.served = 0
        self.processes = set()


class Client:
    """One process's transport to a fake server."""

    def __init__(self, server, process):
        self.server = server
        self.process = process

    async def complete(self, messages, tools=None, **kwargs):
        server = self.server
        server.now += 1
        server.most = max(server.most, server.now)
        server.served += 1
        server.processes.add(self.process)
        try:
            await asyncio.sleep(server.latency_s)
        finally:
            server.now -= 1
        return Reply("DONE", usage=Usage(prompt_tokens=40, completion_tokens=2))


def fleets(processes, servers, admission, **kwargs):
    """One fleet per process, each with its own transports to the servers."""
    return [
        Fleet(
            [Client(s, k) for s in servers],
            admission=admission,
            process=(k, processes),
            **kwargs,
        )
        for k in range(processes)
    ]


def caps(fleet):
    """Each replica's (token cap, request cap) in this process."""
    return [(g["cap_tokens"], g["cap_requests"]) for g in fleet.stats()["gates"]]


def servers(n, latency_s=0.005):
    return [Server(latency_s) for _ in range(n)]


def replicas(n):
    """Transports to servers no test calls."""
    return [Client(Server(0), 0) for _ in range(n)]


HI = [{"role": "user", "content": "hi"}]


# --- the arithmetic -----------------------------------------------------------------


@pytest.mark.parametrize("pack", [False, True])
@pytest.mark.parametrize("replicas", [1, 4, 5])
@pytest.mark.parametrize("processes", [1, 2, 3, 7, 100, 255, 300, 1024])
def test_each_replicas_caps_add_up_exactly_over_the_processes(processes, replicas, pack):
    admission = TokenAdmission(tokens=TOKENS, requests=REQUESTS)
    if replicas * REQUESTS < processes:
        with pytest.raises(ValueError, match="at least one request"):
            fleets(processes, servers(replicas), admission, pack=pack)
        return
    parts = [caps(f) for f in fleets(processes, servers(replicas), admission, pack=pack)]
    for r in range(replicas):
        assert sum(p[r][1] for p in parts) == REQUESTS
        assert sum(p[r][0] for p in parts) == TOKENS
        for tokens, requests in (p[r] for p in parts):
            assert 0 <= requests <= REQUESTS and 0 <= tokens <= TOKENS
            assert (tokens == 0) == (requests == 0)  # no tokens where no request
    # Each process holds an equal part of the fleet, to one request.
    totals = [sum(c[1] for c in p) for p in parts]
    assert min(totals) >= 1 and max(totals) - min(totals) <= 1


def test_100_processes_use_every_request_where_shares_left_a_fifth_idle():
    admission = TokenAdmission(tokens=TOKENS, requests=REQUESTS)
    # 0.6.0's shares, which stay as they were: 2 of 2.56 requests per process,
    # so 200 of each replica's 256 are used.
    old = Fleet(replicas(4), admission=admission, share=1 / 100)
    assert caps(old) == [(2350, 2)] * 4
    parts = [caps(f) for f in fleets(100, servers(4), admission)]
    assert {c[1] for p in parts for c in p} == {2, 3}  # every process at every replica
    for r in range(4):
        assert sum(p[r][1] for p in parts) == REQUESTS


def test_300_processes_never_get_more_requests_than_a_cap_that_shares_passed():
    admission = TokenAdmission(tokens=TOKENS, requests=REQUESTS)
    # 0.6.0's shares, unchanged: 1 request each, so 300 against a cap of 256.
    assert caps(Fleet(replicas(4), admission=admission, share=1 / 300))[0][1] == 1
    parts = [caps(f) for f in fleets(300, servers(4), admission)]
    for r in range(4):
        assert sum(p[r][1] for p in parts) == REQUESTS
    # 3 or 4 requests per process, one at each replica it uses.
    assert {sum(1 for c in p if c[1]) for p in parts} == {3, 4}
    assert {c[1] for p in parts for c in p} == {0, 1}


@pytest.mark.parametrize("processes", [1, 2, 4, 8, 64, 256])
async def test_the_default_matches_shares_where_their_numbers_were_exact(processes):
    admission = TokenAdmission(tokens=262_144, requests=256)
    for k in range(processes):
        exact = Fleet(replicas(4), admission=admission, process=(k, processes))
        shared = Fleet(replicas(4), admission=admission, share=1 / processes)
        assert caps(exact) == caps(shared)
        assert exact.stats()["replicas_used"] == [0, 1, 2, 3]
    # And the same replica for each agent.
    exact = Fleet([Client(s, 0) for s in servers(4, 0)], process=(0, processes))
    shared = Fleet([Client(s, 0) for s in servers(4, 0)], share=1 / processes)
    chosen = {}
    for name, fleet in (("exact", exact), ("shared", shared)):
        records = []
        fleet.add_listener(lambda record, reply: records.append(record.replica))
        for i in range(200):
            await fleet.ainvoke(f"agent-{i:04d}", HI)
        chosen[name] = records
    assert chosen["exact"] == chosen["shared"]


@pytest.mark.parametrize("processes", [1, 2, 4, 8])
def test_packed_processes_hold_whole_caps_on_replicas_of_their_own(processes):
    admission = TokenAdmission(tokens=TOKENS, requests=REQUESTS)
    each = 8 // processes
    used = []
    for k, fleet in enumerate(fleets(processes, servers(8), admission, pack=True)):
        mine = list(range(k * each, (k + 1) * each))
        assert fleet.stats()["replicas_used"] == mine
        assert [c for i, c in enumerate(caps(fleet)) if i in mine] == [(TOKENS, REQUESTS)] * each
        assert all(c == (0, 0) for i, c in enumerate(caps(fleet)) if i not in mine)
        used.extend(mine)
    assert sorted(used) == list(range(8))  # every replica, each to one process


@pytest.mark.parametrize(
    ("processes", "replicas"),
    [(3, 4), (5, 4), (4, 10), (100, 4), (300, 4), (7, 400), (300, 400), (400, 300)],
)
def test_packed_slices_cover_every_replica_from_few_replicas_each(processes, replicas):
    admission = RequestAdmission(REQUESTS)
    users = [0] * replicas
    for fleet in fleets(processes, [None] * replicas, admission, pack=True):
        used = fleet.stats()["replicas_used"]
        assert used == list(range(used[0], used[-1] + 1))  # next to each other
        assert len(used) <= math.ceil(replicas / processes) + 1
        for i in used:
            users[i] += 1
    assert min(users) >= 1  # every replica
    # Each (process, replica) pair beyond one per process is a cut between two
    # processes inside a replica: at most one per replica boundary.
    assert sum(users) <= processes + replicas - 1


def test_a_packed_process_sends_its_agents_in_proportion_to_its_part_of_each_replica():
    # Three processes over four replicas: the first holds replica 0 whole and
    # a third of replica 1.
    fleet = Fleet(replicas(4), admission=RequestAdmission(REQUESTS), process=(0, 3), pack=True)
    assert caps(fleet)[:2] == [(None, 256), (None, 85)]
    counts = [0, 0]
    for i in range(34_100):
        counts[fleet._core.route(f"agent-{i}")] += 1
    assert counts[0] / counts[1] == pytest.approx(256 / 85, rel=0.05)


def test_an_agent_stays_on_its_replica_in_its_process():
    fleet = Fleet(replicas(4), admission=RequestAdmission(REQUESTS), process=(0, 3), pack=True)
    first = [fleet._core.route(f"agent-{i}") for i in range(100)]
    assert [fleet._core.route(f"agent-{i}") for i in range(100)] == first
    assert set(first) == {0, 1}


# --- with fake servers --------------------------------------------------------------


@pytest.mark.parametrize("pack", [False, True])
@pytest.mark.parametrize("processes", [100, 300])
async def test_fake_servers_hold_their_whole_cap_and_never_more(processes, pack):
    """Every process's agents call at once, more than the caps admit: each
    server holds exactly its cap of requests at its busiest, never more."""
    fleet_servers = servers(4)
    admission = TokenAdmission(tokens=TOKENS, requests=REQUESTS)
    processes_fleets = fleets(processes, fleet_servers, admission, pack=pack)

    async def agent(fleet, agent_id):
        for _ in range(2):
            await fleet.ainvoke(agent_id, HI)

    await asyncio.gather(
        *(
            agent(fleet, f"p{k:03d}-agent-{j:03d}")
            for k, fleet in enumerate(processes_fleets)
            for j in range(48)
        )
    )
    assert [s.most for s in fleet_servers] == [REQUESTS] * 4
    assert sum(s.served for s in fleet_servers) == processes * 48 * 2
    for fleet in processes_fleets:
        assert all(g["oversize"] == 0 for g in fleet.stats()["gates"] if g["cap_requests"])
    if pack:  # each process keeps to its own few servers
        assert sum(len(s.processes) for s in fleet_servers) <= processes + 3
    else:
        assert [len(s.processes) for s in fleet_servers] == [min(processes, REQUESTS)] * 4


async def test_a_replica_a_process_does_not_use_has_no_load_and_no_metrics():
    fleet_servers = servers(4, 0)
    fleet = Fleet(
        [Client(s, 0) for s in fleet_servers],
        admission=RequestAdmission(REQUESTS),
        process=(0, 2),
        pack=True,
        name="parts-unused",
    )
    for i in range(20):
        await fleet.ainvoke(f"agent-{i}", HI)
    assert [s.served > 0 for s in fleet_servers] == [True, True, False, False]
    assert fleet.load() == [ReplicaLoad(0, 0, 0)] * 4
    stats = fleet.stats()
    assert stats["process"] == [0, 2] and stats["share"] is None
    assert stats["calls_per_replica"][2:] == [0, 0]
    assert stats["gates"][2] == {"cap_tokens": None, "cap_requests": 0}
    assert {i for (name, i) in fleet_module._loads("requests") if name == "parts-unused"} == {0, 1}
    assert {i for (name, i) in fleet_module._tokens if name == "parts-unused"} == {0, 1}
    fleet.agent_done("agent-1")  # every gate hears it, the unused ones too


# --- shares ---------------------------------------------------------------------------


def test_a_share_is_a_float_with_an_exact_part():
    share = Share(64, 128, 256)
    assert isinstance(share, float) and share == 0.25
    assert share.part(256) == 64 and share.part(235_000) == 58_750
    assert repr(share) == "Share(start=64, stop=128, units=256)"
    # The parts of the shares that tile a replica add up to any cap.
    tiles = [Share(a, b, 7) for a, b in [(0, 2), (2, 3), (3, 7)]]
    assert sum(s.part(1000) for s in tiles) == 1000
    with pytest.raises(ValueError):
        Share(3, 2, 7)


def test_admissions_split_their_caps_exactly_by_a_share():
    gate = TokenAdmission(tokens=1000, requests=10).gate(Share(0, 3, 10))
    assert (gate.cap_tokens, gate.cap_requests) == (300, 3)
    assert RequestAdmission(10).gate(Share(3, 10, 10)).cap_requests == 7
    # A plain float is a 0.6.0 share, floored as before.
    assert TokenAdmission(tokens=1000, requests=10).gate(1 / 3).cap_requests == 3


class Recorded:
    """A 0.6.0 admission of one's own: it reads its share as a float."""

    counts_tokens = False

    def __init__(self):
        self.shares = []

    def gate(self, share):
        self.shares.append(share)
        return RequestAdmission(max(1, int(10 * share))).gate(1.0)


def test_an_admission_of_ones_own_gets_floats_it_can_read_as_before():
    admission = Recorded()
    Fleet(replicas(2), admission=admission, process=(1, 4))
    assert [float(s) for s in admission.shares] == [0.25, 0.25]
    assert all(isinstance(s, Share) for s in admission.shares)


# --- what is refused ------------------------------------------------------------------


@pytest.mark.parametrize("process", [(2, 2), (-1, 2), (0, 0), (0.5, 2), (0, 2.0), 3])
def test_a_process_outside_its_count_is_refused(process):
    with pytest.raises(ValueError, match="process"):
        Fleet(replicas(1), process=process)


def test_a_share_and_a_process_together_are_refused():
    with pytest.raises(ValueError, match="share or process"):
        Fleet(replicas(1), share=0.5, process=(0, 2))


def test_pack_without_a_process_is_refused():
    with pytest.raises(ValueError, match="pack"):
        Fleet(replicas(1), pack=True)


def test_more_processes_than_requests_in_the_fleet_are_refused():
    with pytest.raises(ValueError, match="at least one request"):
        Fleet(replicas(4), admission=RequestAdmission(2), process=(0, 9))
    Fleet(replicas(4), admission=RequestAdmission(2), process=(0, 8))


def test_a_token_cap_too_small_to_split_is_refused():
    with pytest.raises(ValueError, match="token"):
        fleets(200, servers(1), TokenAdmission(tokens=100, requests=REQUESTS))


def test_without_admission_a_packed_process_still_sends_to_its_slice():
    fleet = Fleet(replicas(6), process=(1, 3), pack=True)
    assert fleet.stats()["replicas_used"] == [2, 3]
    assert Fleet(replicas(6), process=(1, 3)).stats()["replicas_used"] == list(range(6))


def test_agent_affinity_without_weights_is_as_before():
    loads = [ReplicaLoad(0, 0, 0)] * 4
    assert AgentAffinity().choose("mat-000001", loads) == fleet_module._stable_hash("mat-000001") % 4


def test_every_http_transport_in_a_process_shares_one_ssl_context():
    """A packed process holds hundreds of replicas it never calls: they must
    cost it little to build. Each SSL context costs about 3 ms and 0.7 MiB."""
    from flowgentic.fleet import HTTPTransport

    transports = [HTTPTransport("m", base_url=f"https://node-{i}:8443/v1", pools=2) for i in range(3)]
    assert len({id(t._ssl) for t in transports}) == 1
