"""Export stage: writes the AF3 job manifest (export_af3_jobs()), called directly."""
from __future__ import annotations

import logging

import flowgentic.metrics as fg

from state import PipelineState


async def run(flow, config: dict, cage_designer_path: str, out_dir, state: PipelineState) -> PipelineState:
    docked_oligomers = state.docked_oligomers
    designed_oligomers = state.designed_oligomers
    rosetta_scores = state.rosetta_scores

    @fg.task(flow, name="export_af3_jobs", description="Write the AF3 job manifest")
    async def task_export_af3(docked_oligomers: list[dict]):
        import CageFlow as _cf
        return _cf.export_af3_jobs(
            designed_oligomers, docked_oligomers, rosetta_scores, config, str(out_dir))

    manifest = await task_export_af3(docked_oligomers)
    logging.info(f"[export_af3] {len(manifest)} job(s) exported")

    state.manifest = manifest
    return state
