"""The unit of work that flows through the pipeline, and its state machine.

A candidate enters at the first step and either reaches the end or leaves
early -- rejected by a gate, found to duplicate another candidate, failed on
an error, or cancelled because its result could no longer change any
decision. Making those exits explicit states rather than implicit deletions
is what lets a run account for every candidate it ever created.

Candidates form a tree. Interface design turns one docked arrangement into
several sequences, so a candidate can spawn children that carry their
parent's accumulated evidence forward. That fan-out is bounded and happens
early: past sequence generation, the work is per-candidate and serial.

A candidate does not track its own position in the pipeline. It records what
it has completed, and the pipeline resolves what to run next from that --
keeping a single source of truth, and avoiding the assumption that every
candidate walks the same linear path.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator

_ids: Iterator[int] = itertools.count(1)


def _next_id() -> str:
    """Sequential, human-readable id.

    Sequential rather than random so a replayed log reads in the same order
    it was written, which matters when comparing two schedulers by eye.
    """
    return f"cand-{next(_ids):05d}"


class CandidateState(str, Enum):
    """Where a candidate stands. Four of these are terminal."""

    PENDING = "pending"
    """Waiting to be worked on at its current step."""

    RUNNING = "running"
    """A task for this candidate is in flight."""

    COMPLETED = "completed"
    """Passed every step in the pipeline."""

    REJECTED = "rejected"
    """Failed a gate. A scientific outcome, not an error."""

    DEDUPLICATED = "deduplicated"
    """Identical to another candidate; that one carries the result."""

    FAILED = "failed"
    """A task raised. An infrastructure outcome, not a scientific one."""

    CANCELLED = "cancelled"
    """Stopped deliberately because its result could not change a decision."""

    SUPERSEDED = "superseded"
    """Replaced by its own children at a fan-out step.

    Distinct from COMPLETED: this candidate did not reach the end of the
    pipeline, it stopped being the unit of work. Its evidence lives on in the
    children that inherited it, and its id remains their grouping key.
    """


TERMINAL_STATES = frozenset(
    {
        CandidateState.COMPLETED,
        CandidateState.REJECTED,
        CandidateState.DEDUPLICATED,
        CandidateState.FAILED,
        CandidateState.CANCELLED,
        CandidateState.SUPERSEDED,
    }
)


@dataclass
class Candidate:
    """One design being carried through the pipeline.

    Args:
        id: Sequential identifier, unique within a run.
        state: Current position in the state machine.
        parent_id: The candidate this one was spawned from, if any.
        duplicate_of: When deduplicated, the candidate whose result this one
            reuses. Recorded rather than dropped so the saved work is
            measurable.
        results: Accumulated per-step output, keyed by step name, in the
            order the steps completed. Doubles as the record of what this
            candidate has already done -- the pipeline resolves what to run
            next from this rather than from a positional index, so a
            candidate that legitimately skips a step (two-component docks
            bypass Rosetta scoring and folding) needs no special case. This
            is also the partial evidence a trajectory model would later
            predict from.
        reason: Why a terminal state was entered, for the event log.
    """

    id: str = field(default_factory=_next_id)
    state: CandidateState = CandidateState.PENDING
    parent_id: str | None = None
    duplicate_of: str | None = None
    results: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None

    @property
    def is_terminal(self) -> bool:
        """Whether this candidate will do no further work."""
        return self.state in TERMINAL_STATES

    @property
    def is_active(self) -> bool:
        """Whether this candidate still has work ahead of it."""
        return not self.is_terminal

    @property
    def completed_steps(self) -> list[str]:
        """Names of the steps this candidate has finished, in order."""
        return list(self.results)

    def has_completed(self, step_name: str) -> bool:
        """Whether this candidate already has a result for `step_name`."""
        return step_name in self.results

    def record(self, step_name: str, result: Any) -> None:
        """Attach one step's output and mark the candidate ready for the next."""
        self.results[step_name] = result
        self.state = CandidateState.PENDING

    def terminate(self, state: CandidateState, reason: str | None = None) -> None:
        """Enter a terminal state.

        Raises:
            ValueError: If `state` is not terminal. Silently accepting a
                non-terminal state here would let a candidate leave the run
                without being accounted for.
        """
        if state not in TERMINAL_STATES:
            raise ValueError(f"{state} is not a terminal state")
        self.state = state
        self.reason = reason

    def spawn(self, n: int) -> list["Candidate"]:
        """Create `n` children inheriting this candidate's evidence.

        Returns:
            The new candidates, each at the same step index as the parent and
            carrying a copy of its results. The parent is left for the caller
            to terminate or keep, since whether it survives fan-out differs
            by step.
        """
        return [
            Candidate(parent_id=self.id, results=dict(self.results))
            for _ in range(n)
        ]
