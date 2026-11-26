"""Exact admission caps per process (``Fleet(process=...)``), and processes
that send to a slice of the replicas (``layout="pack"``), with fake model
servers that count the requests they hold at once."""

import asyncio
import copy
import math
import pickle

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
from flowgentic.fleet import _share_of as share_of

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


@pytest.mark.parametrize("layout", ["spread", "pack"])
@pytest.mark.parametrize("replicas", [1, 4, 5])
@pytest.mark.parametrize("processes", [1, 2, 3, 7, 60, 100, 255, 300, 1024])
def test_each_replicas_caps_add_up_exactly_over_the_processes(processes, replicas, layout):
    admission = TokenAdmission(tokens=TOKENS, requests=REQUESTS)
    if replicas * REQUESTS < processes:
        with pytest.raises(ValueError, match="at least one request"):
            fleets(processes, servers(replicas), admission, layout=layout)
        return
    parts = [caps(f) for f in fleets(processes, servers(replicas), admission, layout=layout)]
    fewest = max(1, REQUESTS // processes)  # what share=1/processes gave
    for r in range(replicas):
        assert sum(p[r][1] for p in parts) == REQUESTS
        assert sum(p[r][0] for p in parts) == TOKENS
        for tokens, requests in (p[r] for p in parts):
            assert 0 <= requests <= REQUESTS and 0 <= tokens <= TOKENS
            assert (tokens == 0) == (requests == 0)  # no tokens where no request
            if requests:
                # Never fewer requests than share gave, and a request's worth
                # of tokens for each request held, rounded down.
                assert requests >= fewest
                assert tokens >= requests * TOKENS // REQUESTS
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
async def test_the_default_matches_shares_where_processes_divide_the_request_cap(
    processes,
):
    """The same requests at every replica, and the same replica for every
    agent; the same tokens too where the processes also divide the token cap
    (235,000: 1, 2, 4 and 8), and otherwise one token apart at most."""
    admission = TokenAdmission(tokens=TOKENS, requests=REQUESTS)
    for k in range(processes):
        exact = Fleet(replicas(4), admission=admission, process=(k, processes))
        shared = Fleet(replicas(4), admission=admission, share=1 / processes)
        assert [c[1] for c in caps(exact)] == [c[1] for c in caps(shared)]
        tokens = [(a[0], b[0]) for a, b in zip(caps(exact), caps(shared))]
        if TOKENS % processes == 0:
            assert all(a == b for a, b in tokens)
        else:
            assert all(0 <= a - b <= 1 for a, b in tokens)
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


def test_the_smallest_token_part_is_a_requests_worth_times_the_fewest_requests():
    """A process's part of a token cap follows its requests: token cap ÷
    request cap for each. With caps that processes do not divide, some parts
    are smaller than share's token cap ÷ processes."""
    admission = TokenAdmission(tokens=TOKENS, requests=REQUESTS)

    def parts(processes, replicas, admission, **kwargs):
        return sorted(
            c for f in fleets(processes, servers(replicas), admission, **kwargs)
            for c in caps(f) if c[1]
        )

    # 100 processes over 4 replicas: 2 or 3 requests at each, so 1,835 tokens
    # at the least, where share=1/100 gave every process 2,350 and 2.
    assert parts(100, 4, admission)[0] == (1835, 2)
    assert caps(Fleet(replicas(4), admission=admission, share=1 / 100))[0] == (2350, 2)
    # Packed, the parts here are 10 or 11 requests, all whole runs.
    assert parts(100, 4, admission, layout="pack")[0] == (9179, 10)
    # 60 processes over one replica of 96 requests: 24 hold one request,
    # 2,447 or 2,448 tokens, and 36 hold two; share gave each 3,916 and 1.
    small = TokenAdmission(tokens=TOKENS, requests=96)
    held = parts(60, 1, small)
    assert sum(1 for c in held if c[1] == 1) == 24
    assert {c[0] for c in held if c[1] == 1} == {2447, 2448}
    assert {c[0] for c in held if c[1] == 2} == {4895, 4896}
    assert caps(Fleet(replicas(1), admission=small, share=1 / 60)) == [(3916, 1)]


@pytest.mark.parametrize("processes", [1, 2, 4, 8])
def test_packed_processes_hold_whole_caps_on_replicas_of_their_own(processes):
    admission = TokenAdmission(tokens=TOKENS, requests=REQUESTS)
    each = 8 // processes
    used = []
    for k, fleet in enumerate(fleets(processes, servers(8), admission, layout="pack")):
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
    for fleet in fleets(processes, [None] * replicas, admission, layout="pack"):
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
    fleet = Fleet(
        replicas(4), admission=RequestAdmission(REQUESTS), process=(0, 3), layout="pack"
    )
    assert caps(fleet)[:2] == [(None, 256), (None, 85)]
    counts = [0, 0]
    for i in range(34_100):
        counts[fleet._core.route(f"agent-{i}")] += 1
    assert counts[0] / counts[1] == pytest.approx(256 / 85, rel=0.05)


def test_an_agent_stays_on_its_replica_in_its_process():
    fleet = Fleet(
        replicas(4), admission=RequestAdmission(REQUESTS), process=(0, 3), layout="pack"
    )
    first = [fleet._core.route(f"agent-{i}") for i in range(100)]
    assert [fleet._core.route(f"agent-{i}") for i in range(100)] == first
    assert set(first) == {0, 1}


# --- with fake servers --------------------------------------------------------------


@pytest.mark.parametrize("layout", ["spread", "pack"])
@pytest.mark.parametrize("processes", [100, 300])
async def test_fake_servers_hold_their_whole_cap_and_never_more(processes, layout):
    """Every process's agents call at once, more than the caps admit: each
    server holds exactly its cap of requests at its busiest, never more."""
    fleet_servers = servers(4)
    admission = TokenAdmission(tokens=TOKENS, requests=REQUESTS)
    processes_fleets = fleets(processes, fleet_servers, admission, layout=layout)

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
    if layout == "pack":  # each process keeps to its own few servers
        assert sum(len(s.processes) for s in fleet_servers) <= processes + 3
    else:
        assert [len(s.processes) for s in fleet_servers] == [min(processes, REQUESTS)] * 4


async def test_a_replica_a_process_does_not_use_has_no_load_and_no_metrics():
    fleet_servers = servers(4, 0)
    fleet = Fleet(
        [Client(s, 0) for s in fleet_servers],
        admission=RequestAdmission(REQUESTS),
        process=(0, 2),
        layout="pack",
        name="parts-unused",
    )
    for i in range(20):
        await fleet.ainvoke(f"agent-{i}", HI)
    assert [s.served > 0 for s in fleet_servers] == [True, True, False, False]
    assert fleet.load() == [ReplicaLoad(0, 0, 0)] * 4
    stats = fleet.stats()
    assert stats["process"] == [0, 2] and stats["share"] is None
    assert stats["calls_per_replica"][2:] == [0, 0]
    assert {i for (name, i) in fleet_module._loads("requests") if name == "parts-unused"} == {0, 1}
    assert {i for (name, i) in fleet_module._tokens if name == "parts-unused"} == {0, 1}
    fleet.agent_done("agent-1")  # every gate hears it, the unused ones too


@pytest.mark.parametrize(
    "admission", [TokenAdmission(TOKENS, REQUESTS), RequestAdmission(REQUESTS), None]
)
def test_every_gates_stats_have_the_same_keys(admission):
    """A replica a process sends nothing to reports the keys of the others,
    with caps and counts of 0, so that code can sum any key over the gates."""
    gates = Fleet(replicas(4), admission=admission, process=(0, 4), layout="pack").stats()[
        "gates"
    ]
    assert all(g.keys() == gates[0].keys() for g in gates)
    assert gates[1]["cap_requests"] == 0
    assert gates[1]["cap_tokens"] == (0 if isinstance(admission, TokenAdmission) else None)
    if admission is not None:
        assert sum(g["oversize"] for g in gates) == 0
        assert all(v in (0, None) for v in gates[1].values())


def test_every_process_fails_alike_on_a_token_cap_below_the_request_cap():
    """A token cap below the request cap would leave some requests no token:
    every process refuses it, whichever its index, or a launcher would get a
    half-started campaign."""
    for processes in (2, 200):
        for k in range(processes):
            with pytest.raises(ValueError, match="token cap"):
                Fleet(
                    replicas(1),
                    admission=TokenAdmission(tokens=100, requests=REQUESTS),
                    process=(k, processes),
                )
    # With share, as in 0.6.0, the caps stay as given.
    Fleet(replicas(1), admission=TokenAdmission(tokens=100, requests=REQUESTS), share=0.5)


# --- shares ---------------------------------------------------------------------------


def test_a_share_is_a_float_with_an_exact_part():
    share = share_of(64, 128, 256)
    assert isinstance(share, float) and share == 0.25
    assert share.part(256) == 64 and share.part(235_000) == 58_750
    # The parts of the shares that tile a replica add up to any cap.
    tiles = [share_of(a, b, 7) for a, b in [(0, 2), (2, 3), (3, 7)]]
    assert sum(s.part(1000) for s in tiles) == 1000
    with pytest.raises(ValueError):
        share_of(3, 2, 7)


def test_a_share_prints_compares_and_hashes_as_its_float():
    share = share_of(1, 3, 6)
    assert str(share) == f"{share}" == "0.3333333333333333" == str(1 / 3)
    assert f"{share:.2f}" == "0.33"
    assert repr(share) == "Share(0.3333333333333333, units 1:3 of 6)"
    # Two parts of a replica as large are equal, whichever units they hold.
    assert share == share_of(2, 4, 6) and hash(share) == hash(share_of(2, 4, 6))
    assert len({share, share_of(2, 4, 6), 1 / 3}) == 1


def test_the_fleet_makes_shares_and_they_survive_pickling():
    with pytest.raises(TypeError, match="Fleet"):
        Share(0, 1, 2)
    for share in (share_of(64, 128, 256), copy.copy(share_of(0, 1, 2))):
        again = pickle.loads(pickle.dumps(share))
        assert type(again) is Share and again == share
        assert again.part(1000) == share.part(1000)


def test_admissions_split_their_caps_exactly_by_a_share():
    gate = TokenAdmission(tokens=1000, requests=10).gate(share_of(0, 3, 10))
    assert (gate.cap_tokens, gate.cap_requests) == (300, 3)
    assert RequestAdmission(10).gate(share_of(3, 10, 10)).cap_requests == 7
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
    assert [f"{s}" for s in admission.shares] == ["0.25", "0.25"]


# --- what is refused ------------------------------------------------------------------


@pytest.mark.parametrize("process", [(2, 2), (-1, 2), (0, 0), (0.5, 2), (0, 2.0), 3])
def test_a_process_outside_its_count_is_refused(process):
    with pytest.raises(ValueError, match="process"):
        Fleet(replicas(1), process=process)


def test_a_share_and_a_process_together_are_refused():
    with pytest.raises(ValueError, match="share or process"):
        Fleet(replicas(1), share=0.5, process=(0, 2))


def test_a_packed_layout_without_a_process_is_refused():
    with pytest.raises(ValueError, match="needs process"):
        Fleet(replicas(1), layout="pack")
    Fleet(replicas(1), layout="spread")  # the default, which share= uses too


@pytest.mark.parametrize("layout", ["packed", "", None, True])
def test_a_layout_of_another_name_is_refused(layout):
    with pytest.raises(ValueError, match="layout"):
        Fleet(replicas(1), process=(0, 1), layout=layout)


def test_more_processes_than_requests_in_the_fleet_are_refused():
    with pytest.raises(ValueError, match="at least one request"):
        Fleet(replicas(4), admission=RequestAdmission(2), process=(0, 9))
    Fleet(replicas(4), admission=RequestAdmission(2), process=(0, 8))


def test_without_admission_a_packed_process_still_sends_to_its_slice():
    fleet = Fleet(replicas(6), process=(1, 3), layout="pack")
    assert fleet.stats()["replicas_used"] == [2, 3]
    assert Fleet(replicas(6), process=(1, 3)).stats()["replicas_used"] == list(range(6))


# --- routing --------------------------------------------------------------------------


def test_agent_affinity_without_weights_is_as_before():
    loads = [ReplicaLoad(0, 0, 0)] * 4
    assert AgentAffinity().choose("mat-000001", loads) == fleet_module._stable_hash("mat-000001") % 4


class Weighted(list):
    """Loads with weights, as any routing sees them."""

    def __init__(self, weights):
        super().__init__([ReplicaLoad(0, 0, 0)] * len(weights))
        self.weights = weights


def test_agent_affinity_reads_only_the_loads_weights():
    affinity = AgentAffinity()
    heavy = Weighted((3, 1))
    counts = [0, 0]
    for i in range(4000):
        counts[affinity.choose(f"agent-{i}", heavy)] += 1
    assert counts[0] / counts[1] == pytest.approx(3, rel=0.15)
    # Other weights, other choices, from the same routing.
    assert {affinity.choose(f"agent-{i}", Weighted((0, 1))) for i in range(50)} == {1}


# --- transports -----------------------------------------------------------------------


def test_every_http_transport_in_a_process_shares_one_ssl_context(monkeypatch):
    """A packed process holds hundreds of replicas it never calls: they must
    cost it little to build. Each SSL context costs about 3 ms and 0.7 MiB.
    A transport made after the certificates' variables change gets theirs."""
    import certifi

    from flowgentic.fleet import HTTPTransport

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    def make():
        return HTTPTransport("m", base_url="https://node-0:8443/v1", pools=2)

    transports = [make() for _ in range(3)]
    assert len({id(t._ssl) for t in transports}) == 1
    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    other = make()
    assert other._ssl is not transports[0]._ssl
    assert make()._ssl is other._ssl
