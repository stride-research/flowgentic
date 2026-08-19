"""Framework-neutral science and infrastructure shared by the AI-HPC demos."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from radical.asyncflow import LocalExecutionBackend

TARGET = 2.75
INITIAL_CENTER = 0.0
INITIAL_RADIUS = 4.0

ResultType = TypeVar("ResultType")


@dataclass(frozen=True)
class CampaignSettings:
    """Scientific and operational constraints shared by every implementation."""

    batch_size: int = 4
    budget: int = 24
    uncertainty_threshold: float = 0.25
    max_cycles: int = 8
    inject_failure: bool = True


class TransientSimulationError(ConnectionError):
    """A recoverable simulation failure injected for the demonstration."""


@dataclass
class SurrogateService:
    """Stateful stand-in for a resident surrogate-model service."""

    instance_id: str = "surrogate-service-01"
    loads: int = 1
    requests: int = 0
    updates: int = 0
    observations: list[dict[str, float]] = field(default_factory=list)

    @classmethod
    async def load(cls) -> "SurrogateService":
        """Simulate loading a model into a persistent AI service."""
        await asyncio.sleep(0.10)
        return cls()

    async def propose(
        self,
        center: float,
        radius: float,
        batch_size: int,
        cycle: int,
    ) -> dict[str, Any]:
        """Propose a deterministic exploration batch around the current center."""
        started = time.perf_counter()
        await asyncio.sleep(0.08)
        self.requests += 1

        if batch_size == 1:
            candidates = [center]
        else:
            candidates = [
                round(center - radius + (2 * radius * i / (batch_size - 1)), 6)
                for i in range(batch_size)
            ]

        return {
            "candidates": candidates,
            "service_id": self.instance_id,
            "service_requests": self.requests,
            "event": timed_event(
                name="surrogate.propose",
                backend="ai",
                cycle=cycle,
                started=started,
            ),
        }

    async def assimilate(
        self,
        results: list[dict[str, Any]],
        radius: float,
        cycle: int,
    ) -> dict[str, Any]:
        """Update model state and return the next campaign parameters."""
        started = time.perf_counter()
        await asyncio.sleep(0.06)
        self.updates += 1
        self.observations.extend(
            {"x": float(result["x"]), "value": float(result["value"])}
            for result in results
        )

        best = min(self.observations, key=lambda item: item["value"])
        next_radius = radius * 0.55
        return {
            "best_x": best["x"],
            "best_value": best["value"],
            "next_radius": next_radius,
            "uncertainty": next_radius,
            "service_id": self.instance_id,
            "service_updates": self.updates,
            "event": timed_event(
                name="surrogate.update",
                backend="ai",
                cycle=cycle,
                started=started,
            ),
        }


_SIMULATION_ATTEMPTS: dict[tuple[int, int], int] = {}


async def evaluate_candidate(
    x: float,
    cycle: int,
    candidate_index: int,
    fail_once: bool = False,
) -> dict[str, Any]:
    """Run the deterministic synthetic simulation used by every demo."""
    key = (cycle, candidate_index)
    attempt = _SIMULATION_ATTEMPTS.get(key, 0) + 1
    _SIMULATION_ATTEMPTS[key] = attempt

    if fail_once and attempt == 1:
        raise TransientSimulationError(
            f"transient node failure for cycle={cycle}, candidate={candidate_index}"
        )

    started = time.perf_counter()
    await asyncio.sleep(0.12 + 0.025 * (candidate_index % 3))
    value = (x - TARGET) ** 2 + 0.002 * (1.0 + math.sin(7.0 * x))

    return {
        "x": x,
        "value": value,
        "attempt": attempt,
        "event": timed_event(
            name=f"simulation[{candidate_index}]",
            backend="compute",
            cycle=cycle,
            started=started,
        ),
    }


async def retry_transient_simulation(
    operation: Callable[[], Awaitable[ResultType]],
) -> ResultType:
    """Apply the baseline's deterministic two-attempt transient-failure policy."""
    try:
        return await asyncio.wait_for(operation(), timeout=5.0)
    except TransientSimulationError:
        await asyncio.sleep(0.05)
        return await asyncio.wait_for(operation(), timeout=5.0)


def timed_event(
    *,
    name: str,
    backend: str,
    cycle: int,
    started: float,
) -> dict[str, Any]:
    """Create timing evidence for the presentation summary."""
    finished = time.perf_counter()
    return {
        "name": name,
        "backend": backend,
        "cycle": cycle,
        "started": started,
        "finished": finished,
        "duration": finished - started,
    }


def initial_scientific_state(
    service: SurrogateService,
    budget: int,
    *,
    implementation: str,
) -> dict[str, Any]:
    """Create framework-neutral state for the surrogate-guided campaign."""
    return {
        "implementation": implementation,
        "cycle": 0,
        "center": INITIAL_CENTER,
        "radius": INITIAL_RADIUS,
        "uncertainty": INITIAL_RADIUS,
        "best_x": None,
        "best_value": None,
        "budget": budget,
        "spent": 0,
        "candidates": [],
        "results": [],
        "trace": [],
        "service": service,
        "decision": "initialize campaign",
    }


async def create_local_backends(compute_workers: int) -> list[LocalExecutionBackend]:
    """Create laptop-safe stand-ins for RHAPSODY AI and compute backends."""
    compute = await LocalExecutionBackend(
        ThreadPoolExecutor(max_workers=compute_workers), name="compute"
    )
    ai = await LocalExecutionBackend(ThreadPoolExecutor(max_workers=2), name="ai")
    return [compute, ai]


def reset_demo_state() -> None:
    """Reset deterministic failure-injection counters between demo runs."""
    _SIMULATION_ATTEMPTS.clear()


def configure_logging(level: int = logging.WARNING) -> None:
    """Keep the default live-demo output compact."""
    logging.getLogger().setLevel(level)
    for logger_name in (
        "asyncio",
        "flowgentic",
        "radical.adr",
        "radical.asyncflow",
    ):
        logging.getLogger(logger_name).setLevel(level)


def print_cycle(state: dict[str, Any], controller: str) -> None:
    """Print one compact campaign-progress line."""
    results = state["results"]
    retries = sum(max(0, int(result["attempt"]) - 1) for result in results)
    print(
        f"cycle={state['cycle']:>2}  controller={controller:<11}  "
        f"simulations={len(results)}  retries={retries}  "
        f"best_x={state['best_x']:.4f}  uncertainty={state['uncertainty']:.4f}  "
        f"budget={state['spent']}/{state['budget']}"
    )


def max_compute_parallelism(trace: list[dict[str, Any]]) -> int:
    """Measure maximum overlap among recorded compute events."""
    events = [event for event in trace if event["backend"] == "compute"]
    points: list[tuple[float, int]] = []
    for event in events:
        points.append((event["started"], 1))
        points.append((event["finished"], -1))

    active = maximum = 0
    for _, delta in sorted(points, key=lambda item: (item[0], item[1])):
        active += delta
        maximum = max(maximum, active)
    return maximum


def write_summary(
    output_dir: Path,
    state: dict[str, Any],
    *,
    controller: str,
    stop_reason: str,
) -> Path:
    """Write machine-readable evidence used after the live run."""
    output_dir.mkdir(parents=True, exist_ok=True)
    service = state["service"]

    summary = {
        "implementation": state.get("implementation", "deterministic"),
        "controller": controller,
        "stop_reason": stop_reason,
        "cycles": state["cycle"],
        "simulations": state["spent"],
        "best_x": state["best_x"],
        "best_value": state["best_value"],
        "uncertainty": state["uncertainty"],
        "service": {
            "instance_id": service.instance_id,
            "loads": service.loads,
            "requests": service.requests,
            "updates": service.updates,
        },
        "execution": {
            "named_backends": ["ai", "compute"],
            "max_compute_parallelism": max_compute_parallelism(state["trace"]),
            "retries": sum(
                max(0, attempts - 1) for attempts in _SIMULATION_ATTEMPTS.values()
            ),
            "events": state["trace"],
        },
    }

    if state.get("agent_trace"):
        summary["agents"] = {
            "model": state.get("agent_model", "unknown"),
            "decisions": state["agent_trace"],
        }

    path = output_dir / "campaign_summary.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return path
