"""Geometry stage: evaluates clash/contact geometry for every docked oligomer,
produces state.geometries, and writes history records for docks that fail the gate.
"""
from __future__ import annotations

import logging

import flowgentic.metrics as fg

from state import PipelineState


async def run(flow, config: dict, cage_designer_path: str, out_dir, state: PipelineState,
               cross_contact_dist: float, cross_clash_dist: float) -> PipelineState:
    import CageFlow
    import records

    docked_oligomers = state.docked_oligomers

    @fg.task(flow, name="evaluate_geometry", description="Clash/contact geometry evaluation")
    async def task_geometry(docked_oligomers: list[dict]):
        from CageFlow import _evaluate_one_geometry
        from records import is_two_component

        clash_dist = config.get("clash_dist", 3.0)
        contact_dist = config.get("contact_dist", 8.0)
        min_contacts = config.get("min_contacts", 3)
        cbeta_noise = config.get("cbeta_noise", 0.5)
        cbeta_min_cb10 = config.get("cbeta_min_cb10", 100)
        clash_carbon_dist = config.get("clash_carbon_dist", 3.0)
        clash_floor_dist = config.get("clash_floor_dist", 2.5)
        clash_atoms = config.get("clash_atoms", "bb_cb")
        steric_gate = config.get("steric_gate", False)
        two_component = is_two_component(docked_oligomers)

        if two_component:
            arg_list = [
                ([d["copies_a"], d["copies_b"]], cross_clash_dist, cross_contact_dist,
                 min_contacts, cage_designer_path, cbeta_noise, cbeta_min_cb10,
                 clash_carbon_dist, clash_floor_dist, steric_gate, clash_atoms)
                for d in docked_oligomers
            ]
        else:
            arg_list = [
                ([d["copies"]], clash_dist, contact_dist, min_contacts, cage_designer_path,
                 cbeta_noise, cbeta_min_cb10, clash_carbon_dist, clash_floor_dist,
                 steric_gate, clash_atoms)
                for d in docked_oligomers
            ]
        return [_evaluate_one_geometry(*args) for args in arg_list]

    if not docked_oligomers:
        raise RuntimeError("dock must run before evaluate_geometry().")

    geometries = await task_geometry(docked_oligomers)

    n_passed = 0
    for i, g in enumerate(geometries):
        if g["passed"]:
            n_passed += 1
        else:
            CageFlow.append_history(
                out_dir, records.dock_outcome(i, docked_oligomers[i], g, status="geometry_failed"))
    logging.info(f"[geometry] {n_passed}/{len(geometries)} passed")

    state.geometries = geometries
    return state
