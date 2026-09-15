"""Append-only event log: the single source of truth a run leaves behind.

Every state transition in a run is recorded here as an immutable, timestamped
fact. Nothing in this module interprets those facts -- metrics are derived
from the log afterwards, never computed inline while the
run is in flight.

That separation is what makes the sequential and asynchronous schedulers
comparable: both emit the same events, only the ordering and concurrency
differ, so any metric computed over the log measures the schedule rather
than the instrumentation.

The on-disk format is JSON Lines, one event per line, chosen to stay
readable by the same tooling as CageFlow's own `history.jsonl`.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterator


class EventType(str, Enum):
    """Every transition worth recording.

    Inherits from `str` so events serialize to readable JSON without a
    custom encoder.
    """

    # Run lifecycle
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"

    # Candidate lifecycle
    CANDIDATE_CREATED = "candidate_created"
    CANDIDATE_ADVANCED = "candidate_advanced"
    CANDIDATE_REJECTED = "candidate_rejected"
    CANDIDATE_DEDUPLICATED = "candidate_deduplicated"

    # Task lifecycle -- the resource-consuming events metrics care about
    TASK_SUBMITTED = "task_submitted"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"

    # Budget accounting. Charges are recorded after the fact, never predicted.
    BUDGET_CHARGED = "budget_charged"


@dataclass(frozen=True)
class Event:
    """One immutable fact about the run.

    Args:
        type: What happened.
        timestamp: Unix epoch seconds, captured at construction.
        candidate_id: Candidate this concerns, if any.
        task_id: Task this concerns, if any.
        step: Name of the `StepSpec` involved, if any.
        resource: Resource pool involved ("cpu"/"gpu"), if any.
        payload: Event-specific detail. Kept deliberately loose so adding a
            field to one event type never forces a schema migration on the
            others; anything a metric depends on should be promoted to a
            real field instead.
    """

    type: EventType
    timestamp: float = field(default_factory=time.time)
    candidate_id: str | None = None
    task_id: str | None = None
    step: str | None = None
    resource: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to a single JSON Lines record."""
        return json.dumps(asdict(self), default=str)


class EventLog:
    """Append-only, in-memory log with optional write-through to disk.

    Deliberately offers no update or delete. A correction is a new event,
    never an edit of an old one.

    Args:
        path: If given, every appended event is also flushed to this file as
            JSON Lines, so a run that crashes still leaves its history behind.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._events: list[Event] = []
        self._path = Path(path) if path is not None else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Truncate: one log file per run, never appended across runs.
            self._path.write_text("")

    def append(self, event: Event) -> Event:
        """Record an event, flushing it to disk if this log is file-backed.

        Returns:
            The same event, so callers can emit and keep a reference in one
            expression.
        """
        self._events.append(event)
        if self._path is not None:
            with self._path.open("a") as handle:
                handle.write(event.to_json() + "\n")
        return event

    def emit(self, type: EventType, **kwargs: Any) -> Event:
        """Construct and append an event in one call."""
        return self.append(Event(type=type, **kwargs))

    def of_type(self, *types: EventType) -> list[Event]:
        """Every event matching any of `types`, in insertion order."""
        wanted = set(types)
        return [e for e in self._events if e.type in wanted]

    def for_task(self, task_id: str) -> list[Event]:
        """Every event concerning one task, in insertion order."""
        return [e for e in self._events if e.task_id == task_id]

    def for_candidate(self, candidate_id: str) -> list[Event]:
        """Every event concerning one candidate, in insertion order."""
        return [e for e in self._events if e.candidate_id == candidate_id]

    @property
    def started_at(self) -> float | None:
        """Timestamp of the first event, or None if the log is empty.

        Metrics measured in "time since start" are relative to this rather
        than to wall-clock, so a replayed log yields the same numbers.
        """
        return self._events[0].timestamp if self._events else None

    @classmethod
    def replay(cls, path: Path | str) -> "EventLog":
        """Load a previously written log back into memory.

        Lets metrics be recomputed over a finished run without re-executing
        it, and is the hook for running a CageFlow `history.jsonl` through
        the same metric code.
        """
        log = cls()
        for line in Path(path).read_text().splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            raw["type"] = EventType(raw["type"])
            log._events.append(Event(**raw))
        return log

    def __iter__(self) -> Iterator[Event]:
        return iter(self._events)

    def __len__(self) -> int:
        return len(self._events)
