"""CageFlow's naive pipeline over AsyncFlow. Stage one of three.

No adaptivity, no budget steering, no intelligence. It exists to show the
pipeline is correct end to end, and to be the baseline everything later is
measured against. Matteo's roadmap puts this first and the agent last.

Three things are worth knowing before reading on.

**The stage implementations are swappable.** Nothing below calls a mock
directly: it calls whatever `stages.simulated` or `stages.actual` provides, and
`--backend` picks. Going from a mocked run to a real cluster run should change
the backend flag and nothing else. Where that turns out not to be true, the
orchestration is carrying a mock-shaped assumption and needs fixing.

**The dependency graph is never declared.** AsyncFlow derives it from the data
flowing between calls, so the fold branch runs alongside the Rosetta branch
without anything saying so.

**Candidates are tracked separately from tasks.** AsyncFlow knows about tasks;
it has no idea a docked pose exists. But the science needs every pose accounted
for, including the ones filtered out early, which is what CageFlow's own
`history.jsonl` is for: every dock's outcome, pass, fail or full progression,
so nothing is silently dropped. That record is produced here.

Run (from the repo root):
    uv run python -m demos.cnio.1_sync.v2.main
    uv run python -m demos.cnio.1_sync.v2.main --backend actual
"""

import argparse
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from radical.asyncflow import LocalExecutionBackend, WorkflowEngine

from flowgentic.candidates import Candidate, CandidateState
from flowgentic.events import EventLog, EventType

from . import report

#: This demo's own output. Laura's real history lives in `cnio/reference/` and
#: must never be written over by a run.
RUNS_DIR = Path(__file__).resolve().parent / "runs"

# Names match CageFlow's config keys so the two can be compared directly.
RADII = [35.0, 40.0, 45.0, 50.0]
SPINS = [0.0, 60.0, 120.0]
N_DOCKS = 2                    # config["n_docks"]
NUM_SEQ_PER_TARGET = 3         # config["num_seq_per_target"]
MIN_CONTACTS = 3               # config["min_contacts"]
ROSETTA_SCORE_CUTOFF = -8.0    # config["rosetta_score_cutoff"]
FOLD_OLIGOMERS = True          # config["fold_oligomers"]
FOLD_TOP_N = 1                 # config["fold_top_n"]

#: Pool sizes. These are the IE cluster's real per-user ceiling expressed as a
#: ratio, not arbitrary: 128 CPU against 2 usable GPUs, and only two of the
#: cluster's three GPUs can run the installed OpenFold at all. Scaled down so a
#: demo finishes, but the lopsidedness is the point and is preserved.
CPU_WORKERS = 8
GPU_WORKERS = 1


def load_backend(name: str):
    """Import the simulated or the actual stage implementations.

    Args:
        name: Either "simulated" or "actual".

    Returns:
        The module providing every stage function.

    Raises:
        ValueError: If `name` is neither backend.
    """
    if name == "simulated":
        from ...stages import simulated
        return simulated
    if name == "actual":
        from ...stages import actual
        return actual
    raise ValueError(f"unknown backend {name!r}, expected 'simulated' or 'actual'")


async def run(log: EventLog, stages) -> list[Candidate]:
    """Execute one campaign, recording everything into `log`.

    Args:
        log: Event sink; also the source of `history.jsonl`.
        stages: The backend module, from `load_backend`.

    Returns:
        Every candidate created, terminal state included.
    """
    cpu = await LocalExecutionBackend(ThreadPoolExecutor(max_workers=CPU_WORKERS), name="cpu")
    gpu = await LocalExecutionBackend(ThreadPoolExecutor(max_workers=GPU_WORKERS), name="gpu")
    flow = await WorkflowEngine.create(backend=[cpu, gpu])

    @flow.function_task(backend="cpu")
    async def dock(i, radius, spin):
        return await stages.dock_oligomer(i, radius, spin)

    @flow.function_task(backend="cpu")
    async def geometry(pose):
        return await stages.evaluate_geometry(pose)

    # Interface design is the one stage with a real hardware choice: Laura
    # confirmed it runs on either and is simply faster on GPU. Pinned to GPU
    # here because stage 1 makes no runtime decisions; choosing between them is
    # stage 3's job, and this is the line it will change.
    @flow.function_task(backend="gpu")
    async def design(pose, n):
        return await stages.design_interface(pose, n)

    @flow.function_task(backend="cpu")
    async def score_designs(designs):
        return await stages.score_designs(designs)

    @flow.function_task(backend="cpu")
    async def rosetta(design):
        return await stages.score_with_rosetta(design)

    @flow.function_task(backend="cpu")
    async def fold_msa(sequence):
        return await stages.fold_msa(sequence)

    @flow.executable_task(backend="gpu")
    async def fold(sequence, msa_handle):
        return stages.fold_command(sequence, msa_handle)

    # Relax (step 10) is deliberately absent: Laura disabled it as too
    # expensive and unpredictable, and CageFlow now raises on configs that
    # still ask for it. `stages.STAGES["relax_qualifying"].enabled` is False and
    # the simulated body is commented out. Do not re-add it to this flow; if it
    # returns, it returns as a standalone pass after a run.

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

    # ---- select. CageFlow's only barrier: it cannot rank until every dock's
    # geometry is in. Rejected poses stay in the record rather than vanishing.
    feasible = [c for c in candidates
                if c.results["pose"]["clashes"] == 0
                and c.results["pose"]["contacts"] >= MIN_CONTACTS]
    top = sorted(feasible, key=lambda c: -c.results["pose"]["contacts"])[:N_DOCKS]
    kept = {c.id for c in top}
    for c in candidates:
        if c.id not in kept:
            reason = "clash or too few contacts" if c not in feasible else "not in top n_docks"
            c.terminate(CandidateState.REJECTED, reason)
            log.emit(EventType.CANDIDATE_REJECTED, candidate_id=c.id,
                     step="select_top_docks", payload={"reason": reason})

    # ---- design fans out: each surviving pose becomes several sequences.
    designed = await asyncio.gather(
        *(design(c.results["pose"], NUM_SEQ_PER_TARGET) for c in top))

    # Group score is recorded and gates nothing, exactly as in CageFlow.
    group_scores = await asyncio.gather(*(score_designs(batch) for batch in designed))

    sequences = []
    for parent, batch, group in zip(top, designed, group_scores):
        parent.record("score_designs", group)
        parent.terminate(CandidateState.SUPERSEDED, "fanned out at design_interface")
        for item in batch:
            child = parent.spawn(1)[0]
            child.record("design_interface", item)
            log.emit(EventType.CANDIDATE_CREATED, candidate_id=child.id,
                     step="design_interface", payload={"parent_id": parent.id})
            sequences.append(child)

    # ---- dedup, per dock. Rosetta threads a sequence onto a specific
    # structure, so the same sequence under two poses is two real calculations.
    # Folding dedups globally instead, below.
    seen, deduped = set(), []
    for c in sequences:
        key = (c.results["design_interface"]["dock"], c.results["design_interface"]["sequence"])
        if key in seen:
            c.terminate(CandidateState.DEDUPLICATED, "duplicate sequence within dock")
            log.emit(EventType.CANDIDATE_DEDUPLICATED, candidate_id=c.id, step="dedup_sequences")
        else:
            seen.add(key)
            deduped.append(c)

    # ---- the gate. Rosetta is the only pass/fail in the whole pipeline.
    scored = await asyncio.gather(*(rosetta(c.results["design_interface"]) for c in deduped))
    for c, s in zip(deduped, scored):
        c.record("score_with_rosetta", {"ddg": s["ddg"], "ddg_noise_sigma": s["ddg_noise_sigma"]})

    # ---- fold. Nothing here needs Rosetta: not the fold itself (sequence
    # only), and not the shortlist either -- CageFlow's
    # `_rank_sequences_for_fold` prefers ddG but falls back to the ProteinMPNN
    # score, "so folding still works on a pipeline that never reached Rosetta,
    # just ranked on weaker evidence".
    #
    # It runs after Rosetta here purely because stage 1 is sequential. Using
    # the fallback ranking instead would let this whole branch start as soon as
    # designs exist, at the cost of sometimes folding a sequence a later ddG
    # would have rejected. That is a quality trade, not a correctness one, and
    # deciding it per candidate is stage 2's first real target.
    folded: dict[str, dict] = {}
    if FOLD_OLIGOMERS:
        by_dock: dict[int, list[Candidate]] = {}
        for c in deduped:
            by_dock.setdefault(c.results["design_interface"]["dock"], []).append(c)

        # Global dedup here, unlike the per-dock dedup above: a fold depends on
        # the sequence alone, and RMSD superimposes before measuring, so the
        # dock's placement drops out. Holds only while docking moves a fixed
        # scaffold.
        to_fold = {}
        for group in by_dock.values():
            best = sorted(group, key=lambda c: c.results["score_with_rosetta"]["ddg"])[:FOLD_TOP_N]
            for c in best:
                to_fold.setdefault(c.results["design_interface"]["sequence"], c)

        async def fold_branch(sequence):
            return await fold(sequence, await fold_msa(sequence))

        results = await asyncio.gather(*(fold_branch(s) for s in to_fold))
        folded = {seq: json.loads(out) for seq, out in zip(to_fold, results)}

    # ---- collect. Only ddG gates; fold metrics are recorded and gate nothing,
    # which is how CageFlow behaves and how Laura wants it to stay. Writing them
    # down matters: without this they would be computed, paid for, and lost.
    for c in deduped:
        d = c.results["score_with_rosetta"]
        seq = c.results["design_interface"]["sequence"]
        f = folded.get(seq)
        if f:
            c.record("fold_inference", f)

        log.emit(
            EventType.CANDIDATE_ADVANCED,
            candidate_id=c.id,
            step="score_with_rosetta",
            payload={
                "label": f"{seq}\ndock {c.results['design_interface']['dock']}",
                "sequence": seq,
                "dock": c.results["design_interface"]["dock"],
                "mpnn_score": round(c.results["design_interface"]["mpnn_score"], 4),
                "ddg": round(d["ddg"], 3),
                "ddg_noise_sigma": d["ddg_noise_sigma"],
                "plddt": f["plddt"] if f else None,
                "iptm": f["iptm"] if f else None,
                "rmsd": f["rmsd"] if f else None,
                "folded": f is not None,
                "gated_on": "ddg",
                "cutoff": ROSETTA_SCORE_CUTOFF,
            },
        )

        if d["ddg"] <= ROSETTA_SCORE_CUTOFF:
            c.terminate(CandidateState.COMPLETED, "passed rosetta cutoff")
        else:
            c.terminate(CandidateState.REJECTED, f"ddG {d['ddg']:.2f} above cutoff")
            log.emit(EventType.CANDIDATE_REJECTED, candidate_id=c.id,
                     step="score_with_rosetta", payload={"ddg": round(d["ddg"], 3)})

    log.emit(EventType.RUN_FINISHED)
    await flow.shutdown()
    return candidates + sequences


def summarise(everything: list[Candidate]) -> None:
    """Print the qualifying table, then account for every candidate created."""
    scored = [c for c in everything if c.has_completed("score_with_rosetta")]
    print(f"\n  {'seq':>6} {'ddG':>7} {'+/-':>5} {'pLDDT':>6} {'RMSD':>6}   outcome")
    for c in sorted(scored, key=lambda c: c.results["score_with_rosetta"]["ddg"]):
        d = c.results["score_with_rosetta"]
        f = c.results.get("fold_inference")
        plddt = f"{f['plddt']:>6.2f}" if f else "     -"
        rmsd = f"{f['rmsd']:>6.2f}" if f else "     -"
        print(f"  {c.results['design_interface']['sequence']:>6} {d['ddg']:>7.2f} "
              f"{d['ddg_noise_sigma']:>5.2f} {plddt} {rmsd}   {c.state.value}")

    counts: dict[str, int] = {}
    for c in everything:
        counts[c.state.value] = counts.get(c.state.value, 0) + 1
    print(f"\n  {len(everything)} candidates, all accounted for: {counts}")
    print("  full per-candidate record -> runs/history.jsonl")


async def main() -> None:
    """Run one campaign, then render its metrics."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", default="simulated", choices=["simulated", "actual"],
                        help="which stage implementations to run")
    args = parser.parse_args()

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    history = RUNS_DIR / "history.jsonl"

    stages = load_backend(args.backend)
    print(f"  backend: {args.backend}")

    everything = await run(EventLog(history), stages)
    summarise(everything)
    figure = report.write(history, RUNS_DIR / "reports")
    print(f"  science metrics figure   -> {figure.relative_to(RUNS_DIR.parent)}")


if __name__ == "__main__":
    asyncio.run(main())
