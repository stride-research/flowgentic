"""Design stage: runs ProteinMPNN on each selected dock's interface and produces
state.designed_oligomers.
"""
from __future__ import annotations

import asyncio

import flowgentic.metrics as fg

from state import PipelineState


async def run(flow, config: dict, cage_designer_path: str, out_dir, state: PipelineState) -> PipelineState:
    import CageFlow
    import records

    docked_oligomers = state.docked_oligomers
    geometries = state.geometries
    selected_indices = state.selected_indices
    mpnn_dir = config.get("mpnn_dir", "ProteinMPNN")
    resource = "cpu" if config.get("mpnn_force_cpu") else "gpu"

    @fg.task(flow, name="design_interface", resource=resource,
             description="ProteinMPNN sequence design for one dock's interface")
    async def task_design(dock_index: int, docked_oligomer: dict, geometry: dict):
        from CageFlow import _design_one_interface
        import records as _records

        residue_sets = _records.interface_residue_sets(geometry)
        dock_out_dir = out_dir / f"dock_{dock_index}"
        mpnn_force_cpu = config.get("mpnn_force_cpu", False)

        return dock_index, _design_one_interface(
            docked_oligomer, residue_sets, config, str(dock_out_dir), mpnn_dir,
            bias=config.get("bias"), force_cpu=mpnn_force_cpu,
        )

    design_tasks = [
        task_design(i, docked_oligomers[i], geometries[i]) for i in selected_indices
    ]
    design_results = await asyncio.gather(*design_tasks)

    designed_oligomers: dict[int, list] = {}
    for dock_index, result in design_results:
        geometry = geometries[dock_index]
        if result["ok"]:
            designed_oligomers[dock_index] = result["designs"]
            summary_status, design_summary = "designed", CageFlow._summarize_design(result["designs"])
        else:
            summary_status, design_summary = "design_failed", None
        CageFlow.append_history(out_dir, records.dock_outcome(
            dock_index, docked_oligomers[dock_index], geometry, status=summary_status,
            design_summary=design_summary, error=result.get("error")))
    print(f"[design] {len(designed_oligomers)}/{len(selected_indices)} dock(s) designed")

    state.designed_oligomers = designed_oligomers
    return state
