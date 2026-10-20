"""Score-designs stage: CageFlow's own per-dock design summary/ranking, called directly."""
from __future__ import annotations

import flowgentic.metrics as fg

from state import PipelineState


async def run(flow, config: dict, cage_designer_path: str, out_dir, state: PipelineState) -> PipelineState:
    docked_oligomers = state.docked_oligomers
    geometries = state.geometries
    designed_oligomers = state.designed_oligomers

    @fg.task(flow, name="score_designs", description="Per-dock design summary/ranking")
    async def task_score_designs(docked_oligomers: list[dict], geometries: list[dict]):
        import CageFlow as _cf
        return _cf.score_designs(
            designed_oligomers, docked_oligomers, geometries, config, str(out_dir))

    scored_designs = await task_score_designs(docked_oligomers, geometries)

    state.scored_designs = scored_designs
    return state
