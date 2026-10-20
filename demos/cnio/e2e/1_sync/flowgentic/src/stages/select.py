"""Select stage: CageFlow's own select_top_docks(), called directly and unmodified --
it never took an Executor/Budget in the first place, so there is nothing to vendor here.
"""
from __future__ import annotations

import flowgentic.metrics as fg

from state import PipelineState


async def run(flow, config: dict, cage_designer_path: str, out_dir, state: PipelineState) -> PipelineState:
    docked_oligomers = state.docked_oligomers
    geometries = state.geometries

    @fg.task(flow, name="select_top_docks",
             description="Rank and filter docks; pure sort/filter over in-memory data")
    async def task_select(docked_oligomers: list[dict], geometries: list[dict]):
        import CageFlow as _cf
        return _cf.select_top_docks(docked_oligomers, geometries, config, str(out_dir))

    selected_indices = await task_select(docked_oligomers, geometries)
    print(f"[select] selected {selected_indices}")

    state.selected_indices = selected_indices
    return state
