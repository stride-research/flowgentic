"""CageFlow's naive pipeline, mocked, over AsyncFlow.

Stage one of the roadmap: no adaptivity, no budget steering, no intelligence.
It exists to show the pipeline is correct end to end, and to be the baseline
everything later is measured against.

Two things are worth knowing before reading on.

**The dependency graph is never declared.** AsyncFlow derives it from the data
flowing between calls, so the fold branch runs alongside the Rosetta branch
without anything saying so -- both need only the designed sequence, neither
needs the other's output.

**Candidates are tracked separately from tasks.** AsyncFlow knows about tasks;
it has no idea a docked pose exists. But the science needs every pose
accounted for, including the ones filtered out early, which is what CageFlow's
own `history.jsonl` is for: "every dock's outcome -- pass, fail, or full
progression -- so nothing is silently dropped". That record is produced here.

Run:  uv run python demos/cnio/v2/main.py
"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flowgentic.demos.cnio.v2 import mocks
from radical.asyncflow import LocalExecutionBackend, WorkflowEngine

from flowgentic.demos.cnio.v2 import report
from flowgentic.candidates import Candidate, CandidateState
from flowgentic.events import EventLog, EventType

# Configuration parameters for the stubs
RADII = [35.0, 40.0, 45.0, 50.0]
SPINS = [0.0, 60.0, 120.0]
N_DOCKS_KEPT = 2
N_SEQUENCES = 3
MIN_CONTACTS = 3
ROSETTA_CUTOFF = -8.0


async def main() -> None:
    log = EventLog(Path.cwd() / "history.jsonl")
    cpu = await LocalExecutionBackend(ThreadPoolExecutor(max_workers=2), name="cpu")
    gpu = await LocalExecutionBackend(ThreadPoolExecutor(max_workers=1), name="gpu")
    flow = await WorkflowEngine.create(backend=[cpu, gpu])

    @flow.function_task(backend="cpu")
    async def dock(i, radius, spin):
        return await mocks.dock_oligomer(i, radius, spin)

    @flow.function_task(backend="cpu")
    async def geometry(pose):
        return await mocks.evaluate_geometry(pose)

    @flow.function_task(backend="gpu")
    async def design(pose, n):
        return await mocks.design_interface(pose, n)

    @flow.function_task(backend="cpu")
    async def relax(candidate):
        return await mocks.relax(candidate)

    @flow.function_task(backend="cpu")
    async def rosetta(relaxed):
        return await mocks.score_with_rosetta(relaxed)

    @flow.function_task(backend="cpu")
    async def msa(sequence):
        return await mocks.fetch_msa(sequence)

    @flow.executable_task(backend="gpu")
    async def fold(sequence, msa_handle):
        return mocks.fold_command(sequence, msa_handle)

    log.emit(EventType.RUN_STARTED)

    # ---- dock every grid point, then evaluate each pose's geometry.
    grid = [(r, s) for r in RADII for s in SPINS]
    poses = await asyncio.gather(*(geometry(dock(i, r, s)) for i, (r, s) in enumerate(grid)))

    candidates = []
    for pose in poses:
        c = Candidate()
        c.record("dock_oligomer", {"radius": pose["radius"], "spin": pose["spin"]})
        c.record("evaluate_geometry", {"contacts": pose["contacts"], "clashes": pose["clashes"]})
        c.results["pose"] = pose
        log.emit(EventType.CANDIDATE_CREATED, candidate_id=c.id, step="dock_oligomer")
        candidates.append(c)

    # ---- select. Rejected poses stay in the record rather than disappearing.
    feasible = [c for c in candidates
                if c.results["pose"]["clashes"] == 0
                and c.results["pose"]["contacts"] >= MIN_CONTACTS]
    top = sorted(feasible, key=lambda c: -c.results["pose"]["contacts"])[:N_DOCKS_KEPT]
    kept = {c.id for c in top}
    for c in candidates:
        if c.id not in kept:
            reason = "clash or too few contacts" if c not in feasible else "not in top n_docks"
            c.terminate(CandidateState.REJECTED, reason)
            log.emit(EventType.CANDIDATE_REJECTED, candidate_id=c.id,
                     step="select_top_docks", payload={"reason": reason})

    # ---- design fans out: each surviving pose becomes several sequences.
    designed = await asyncio.gather(*(design(c.results["pose"], N_SEQUENCES) for c in top))
    sequences = []
    for parent, batch in zip(top, designed):
        parent.terminate(CandidateState.SUPERSEDED, "fanned out at design_interface")
        for item in batch:
            child = parent.spawn(1)[0]
            child.record("design_interface", item)
            log.emit(EventType.CANDIDATE_CREATED, candidate_id=child.id,
                     step="design_interface", payload={"parent_id": parent.id})
            sequences.append(child)

    # ---- dedup within a pose only. Rosetta threads a sequence onto a specific
    # structure, so the same sequence under two poses is two real calculations.
    seen, deduped = set(), []
    for c in sequences:
        key = (c.results["design_interface"]["dock"], c.results["design_interface"]["sequence"])
        if key in seen:
            c.terminate(CandidateState.DEDUPLICATED, "duplicate sequence within pose")
            log.emit(EventType.CANDIDATE_DEDUPLICATED, candidate_id=c.id, step="dedup_sequences")
        else:
            seen.add(key)
            deduped.append(c)

    # ---- the branch. Both halves need only the designed sequence.
    #
    # Rosetta: relax the structure, then score it.
    ddg = [rosetta(relax(c.results["design_interface"])) for c in deduped]

    # Fold: structure-blind, so it never waits for relax. Cached on the
    # sequence alone -- two poses sharing a sequence fold once.
    async def fold_branch(sequence):
        return await fold(sequence, await msa(sequence))

    folds = {}
    for c in deduped:
        seq = c.results["design_interface"]["sequence"]
        folds.setdefault(seq, asyncio.create_task(fold_branch(seq)))

    scored = await asyncio.gather(*ddg)
    folded = {seq: json.loads(out) for seq, out in zip(folds, await asyncio.gather(*folds.values()))}

    # ---- collect. Only the Rosetta ddG gates; fold metrics are recorded but
    # gate nothing, which is how CageFlow behaves today.
    for c, s in zip(deduped, scored):
        f = folded[c.results["design_interface"]["sequence"]]
        c.record("score_with_rosetta", {"ddg": s["ddg"]})
        c.record("fold_inference", f)

        # Every measurement goes into the record, not just the gate outcome.
        # The fold metrics gate nothing today -- CNIO records pLDDT, ipTM and
        # RMSD but filters on none of them -- so without writing them here
        # they would be computed, paid for, and lost.
        log.emit(
            EventType.CANDIDATE_ADVANCED,
            candidate_id=c.id,
            step="score_with_rosetta",
            payload={
                "sequence": c.results["design_interface"]["sequence"],
                "dock": c.results["design_interface"]["dock"],
                "mpnn_score": round(c.results["design_interface"]["mpnn_score"], 4),
                "ddg": round(s["ddg"], 3),
                "ddg_noise_sigma": s["ddg_noise_sigma"],
                "plddt": f["plddt"],
                "iptm": f["iptm"],
                "rmsd": f["rmsd"],
                "gated_on": "ddg",
                "cutoff": ROSETTA_CUTOFF,
            },
        )

        if s["ddg"] <= ROSETTA_CUTOFF:
            c.terminate(CandidateState.COMPLETED, "passed rosetta cutoff")
        else:
            c.terminate(CandidateState.REJECTED, f"ddG {s['ddg']:.2f} above cutoff")
            log.emit(EventType.CANDIDATE_REJECTED, candidate_id=c.id,
                     step="score_with_rosetta", payload={"ddg": round(s["ddg"], 3)})

    log.emit(EventType.RUN_FINISHED)
    await flow.shutdown()
    summarise(candidates + sequences)

    figure = report.write(Path.cwd() / "history.jsonl", Path.cwd() / "reports")
    print(f"  science metrics figure   -> {figure.relative_to(Path.cwd())}")


def summarise(everything: list[Candidate]) -> None:
    """Print the qualifying table, then account for every candidate created."""
    scored = [c for c in everything if c.has_completed("score_with_rosetta")]
    print(f"\n  {'seq':>6} {'ddG':>7} {'pLDDT':>6} {'RMSD':>6}   outcome")
    for c in sorted(scored, key=lambda c: c.results["score_with_rosetta"]["ddg"]):
        d, f = c.results["score_with_rosetta"], c.results["fold_inference"]
        print(f"  {c.results['design_interface']['sequence']:>6} {d['ddg']:>7.2f} "
              f"{f['plddt']:>6.2f} {f['rmsd']:>6.2f}   {c.state.value}")

    counts: dict[str, int] = {}
    for c in everything:
        counts[c.state.value] = counts.get(c.state.value, 0) + 1
    print(f"\n  {len(everything)} candidates, all accounted for: {counts}")
    print("  full per-candidate record -> history.jsonl")


if __name__ == "__main__":
    asyncio.run(main())
