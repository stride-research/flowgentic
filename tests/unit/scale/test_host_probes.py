"""The host's probes keep running values and a fixed histogram, not every
sample: a host that serves for days keeps the same memory as one that just
started, and its report keeps its fields, exact but for the lag's p99."""

import asyncio
import random
import sys

import pytest

from flowgentic.host import Agent, AgentHost, Finished, Stagger
from flowgentic.host._probes import CollectorPauses, LoopLag

INTERVAL = LoopLag.INTERVAL


def stored(probe) -> int:
    """The bytes a probe holds: its attributes, and what its containers hold."""

    def size(value) -> int:
        if isinstance(value, dict):
            return sys.getsizeof(value) + sum(size(k) + size(v) for k, v in value.items())
        if isinstance(value, list):
            return sys.getsizeof(value) + sum(size(v) for v in value)
        return sys.getsizeof(value)  # an array's buffer included

    return sum(size(v) for k, v in vars(probe).items() if k != "_loop")


def lags(rng: random.Random, n: int) -> list[float]:
    """Mostly a loop that wakes within a millisecond, sometimes blocked for
    tens of milliseconds, now and then for seconds."""
    out = []
    for _ in range(n):
        u = rng.random()
        if u < 0.9:
            out.append(rng.uniform(0.0, 0.002))
        elif u < 0.99:
            out.append(rng.expovariate(1 / 0.03))
        else:
            out.append(rng.uniform(0.5, 5.0))
    return out


# --- the old summaries, from every sample: the reference -----------------------------


def exact_p99(samples: list[float]) -> float:
    total = sum(INTERVAL + x for x in samples)

    def share_over(x: float) -> float:
        return sum(max(v - x, 0.0) for v in samples) / total

    if share_over(0.0) <= 0.01:
        return 0.0
    lo, hi = 0.0, max(samples)
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if share_over(mid) > 0.01 else (lo, mid)
    return hi


def exact_lag_summary(samples: list[float]) -> dict:
    total = sum(INTERVAL + x for x in samples)
    return {
        "samples": len(samples),
        "max_ms": round(1e3 * max(samples), 1),
        "time_weighted_p99_ms": round(1e3 * exact_p99(samples), 1),
        "share_over_100ms": round(sum(max(v - 0.1, 0.0) for v in samples) / total, 4),
    }


def exact_pause_summary(pauses: list[tuple[float, int]]) -> dict:
    out: dict = {"pauses": len(pauses), "total_s": round(sum(p for p, _ in pauses), 3)}
    for g in (0, 1, 2):
        xs = [p for p, gen in pauses if gen == g]
        if xs:
            out[f"gen{g}"] = {
                "count": len(xs),
                "total_s": round(sum(xs), 3),
                "max_ms": round(1e3 * max(xs), 1),
            }
    return out


# --- memory ------------------------------------------------------------------------


def test_a_serving_host_s_probes_keep_the_same_memory_however_long_it_runs():
    """A day of lag samples and a million collections: the probes hold what
    they held after the first thousand."""
    rng = random.Random(1)
    lag, pauses = LoopLag(), CollectorPauses()
    for x in lags(rng, 1_000):
        lag.add(x)
        pauses.add(x / 10, rng.choice((0, 0, 0, 1, 2)))
    first = stored(lag), stored(pauses)
    day = 864_000  # 100 ms samples
    for i, x in enumerate(lags(rng, day)):
        lag.add(x)
        pauses.add(x / 10, rng.choice((0, 0, 0, 1, 2, -1)))
        if i < 100_000:
            pauses.add(x / 100, 0)
    assert (stored(lag), stored(pauses)) == first
    assert lag.samples == 1_000 + day and pauses.pauses == 1_000 + day + 100_000
    # About 33 KB for the lag, whatever the time; 8 bytes a sample before.
    assert stored(lag) < 40_000 < 8 * day
    assert stored(pauses) < 2_000


# --- the summaries -----------------------------------------------------------------


@pytest.mark.parametrize("seed", range(6))
def test_the_lag_summary_keeps_its_fields_exact_but_for_the_p99(seed):
    rng = random.Random(seed)
    samples = lags(rng, rng.choice((1, 50, 2_000, 20_000)))
    if seed == 5:
        samples = [rng.uniform(0.0, 0.00005) for _ in samples]  # an idle loop
    lag = LoopLag()
    for x in samples:
        lag.add(x)
    got, want = lag.summary(), exact_lag_summary(samples)
    assert got.keys() == want.keys()
    p99 = got.pop("time_weighted_p99_ms"), want.pop("time_weighted_p99_ms")
    assert got == want  # samples, max_ms and share_over_100ms: exact
    # The p99: within 1 % or 0.1 ms, whichever is larger; then rounded to 0.1 ms.
    exact = 1e3 * exact_p99(samples)
    assert abs(1e3 * lag._p99() - exact) <= max(0.01 * exact, 0.1) + 1e-9
    assert abs(p99[0] - p99[1]) <= max(0.01 * exact, 0.1) + 0.1


@pytest.mark.parametrize(
    "samples",
    [
        [0.0] * 100,  # never late
        [0.0] * 98 + [0.5, 3.0],  # two blocks, with empty buckets between them
        [0.02] * 1000,  # every sample in one bucket
        [1e-4 * 1.01**k for k in range(2_000)],  # one sample per bucket
        [0.0] * 99 + [7_200.0],  # one two-hour block
        [0.0] * 99 + [200_000.0],  # in the last bucket, over a day
    ],
    ids=["idle", "two-blocks", "one-bucket", "every-bucket", "two-hours", "two-days"],
)
def test_the_lag_p99_holds_its_precision_at_the_histogram_s_edges(samples):
    lag = LoopLag()
    for x in samples:
        lag.add(x)
    exact = exact_p99(samples)
    assert abs(lag._p99() - exact) <= max(0.01 * exact, 1e-4) + 1e-12
    assert lag.summary()["max_ms"] == round(1e3 * max(samples), 1)


def test_the_pause_summary_is_exact():
    rng = random.Random(7)
    pauses = [(rng.expovariate(1 / 0.002), rng.choice((0, 0, 1, 2, -1))) for _ in range(5000)]
    probe = CollectorPauses()
    for p, g in pauses:
        probe.add(p, g)
    assert probe.summary() == exact_pause_summary(pauses)
    assert CollectorPauses().summary() == {"pauses": 0, "total_s": 0.0}
    assert LoopLag().summary() == {"samples": 0}


async def test_a_host_reports_the_same_fields():
    class Sleepy:
        async def start(self, agent):
            await asyncio.sleep(0.3)  # long enough for a 100 ms lag sample
            return Finished(None)

    report = await AgentHost(Sleepy(), stagger=Stagger(batch=10, interval_s=0)).run(
        [Agent("a", None)]
    )
    assert report.loop_lag.keys() == {
        "samples",
        "max_ms",
        "time_weighted_p99_ms",
        "share_over_100ms",
    }
    assert report.loop_lag["samples"] >= 1  # a slow machine may take only one
    assert {"pauses", "total_s", "settings", "freeze"} <= report.collector.keys()
