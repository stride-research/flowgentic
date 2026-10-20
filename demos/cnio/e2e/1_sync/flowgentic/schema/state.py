"""Shared pipeline state threaded between stage modules.

Each field is produced by exactly one stage (named below) and consumed by every later
stage that needs it. `None` means "that stage hasn't run yet" -- main.py's call sequence
is what enforces that a stage never runs before the fields it reads are set; this
dataclass only carries the data, it doesn't check the ordering.

Values resolved from config rather than produced by a stage (e.g. cross_contact_dist,
cross_clash_dist, needed by both dock and geometry) are passed as explicit arguments
instead of living here -- this dataclass is for stage OUTPUTS only.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PipelineState:
    docked_oligomers: list[dict] | None = None          # dock
    geometries: list[dict] | None = None                # geometry
    selected_indices: list[int] | None = None           # select
    designed_oligomers: dict[int, list] | None = None   # design
    scored_designs: dict | None = None                  # score_designs
    rosetta_scores: dict[int, dict] | None = None       # rosetta
    fold_results: dict[int, dict] | None = None         # fold
    manifest: list | None = None                        # export
