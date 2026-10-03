"""Stand-ins for CageFlow's steps, written to be deleted.

Each is a placeholder for one real step. When a step's real invocation is
confirmed, `stages/actual/` takes over and nothing else changes.

Durations come from `stages.STAGES`, not from a second table here, so the mock
and the scheduler cannot disagree about what a step costs. They are divided by
SPEEDUP so a demo finishes in seconds; set it to 1.0 for real timings.
"""

import asyncio
import hashlib
import math
import random
import shlex
import sys
from pathlib import Path

from .. import ROSETTA_NOISE_KCAL, STAGES

#: Divides every duration. 1.0 runs at real speed.
SPEEDUP = 400.0

#: Stand-in for steps the registry never timed. All are marked trivial.
UNMEASURED_S = 0.05

_rng = random.Random(7)


async def _work(stage: str) -> None:
    """Occupy a worker for roughly what the real step costs."""
    await asyncio.sleep((STAGES[stage].cost_s or UNMEASURED_S) / SPEEDUP)


async def dock_oligomer(index: int, radius: float, spin: float) -> dict:
    """Place the scaffold at one point on the docking grid.

    The shape is taken from CNIO's real `history.jsonl`, not invented: a dock
    is an index plus a `solution` carrying the degrees of freedom searched
    (spin, lat, lon, radius) and the objective's verdict on them (score,
    contacts, min_clash, helix_contacts).

    `score` here is the DOCKING objective, and it is what select_top_docks
    ranks on. The contact count in `solution` is the docking objective's own;
    evaluate_geometry recomputes contacts independently and reports them under
    different names. Two numbers, two sources, deliberately.
    """
    await _work("dock_oligomer")
    contacts = _rng.randint(0, 60)
    return {
        "dock_index": index,
        "solution": {
            "spin": spin, "lat": 0.0, "lon": 0.0, "radius": radius,
            "score": contacts * _rng.uniform(0.2, 0.4),
            "contacts": contacts,
            "min_clash": _rng.uniform(2.0, 4.0),
            "helix_contacts": _rng.randint(0, 5),
        },
    }


async def dock_two_component(index: int, radius_a: float, radius_b: float,
                             spin_a: float, spin_b: float) -> dict:
    """Place two different building blocks at one point on the joint grid.

    Selected by setting `scaffold_pdb_b` in the config. Two blocks have to be
    coordinated at once, so this is not a duplication of dock_oligomer.
    """
    await _work("dock_two_component")
    return {
        "dock_index": index,
        "mode": "two_component",
        "solution": {"spin_a": spin_a, "spin_b": spin_b,
                     "radius_a": radius_a, "radius_b": radius_b,
                     "score": _rng.uniform(5.0, 20.0)},
    }


async def evaluate_geometry(dock: dict, min_contacts: int = 3) -> dict:
    """Independent clash and contact check on one docked pose.

    Field names are CNIO's, from their real history.jsonl: `n_clashes`,
    `n_contacts`, `n_interface_residues`, and `passed`. Note these are NOT the
    same numbers as the docking solution's `contacts` -- this is a separate,
    authoritative check, which is why it has its own names.

    `passed` is the only thing select_top_docks filters on.
    """
    await _work("evaluate_geometry")
    n_clashes = _rng.randint(0, 3)
    n_contacts = _rng.randint(0, 60)
    return {
        "passed": n_clashes == 0 and n_contacts >= min_contacts,
        "n_clashes": n_clashes,
        "n_contacts": n_contacts,
        "n_interface_residues": n_contacts,
    }


async def select_top_docks(docked_oligomers: list[dict], geometries: list[dict],
                           n_docks: int) -> list[int]:
    """Rank the arrangements and keep the best few.

    Matches CageFlow.py:1076: keep only poses whose geometry `passed`, rank
    those by the docking `score` DESCENDING, take the first `n_docks`, return
    the indices sorted. Note the ranking key is the docking score, not the
    contact count -- getting that wrong silently changes which designs the
    whole run spends its budget on.

    The pipeline's only hard barrier: it cannot rank until every dock is in.
    """
    await _work("select_top_docks")
    passed = [i for i, g in enumerate(geometries) if g["passed"]]
    ranked = sorted(passed, key=lambda i: docked_oligomers[i]["solution"]["score"], reverse=True)
    return sorted(ranked[:n_docks])


async def design_interface(dock: dict, n_sequences: int) -> list[dict]:
    """Redesign the interface residues into several candidate sequences.

    Returns a list because this is the fan-out: one pose yields many sequences
    and everything downstream is per-sequence. Sequences are drawn from a small
    alphabet so duplicates occur, as they do in practice, because ProteinMPNN
    samples at low temperature to stay near the native sequence.
    """
    await _work("design_interface")
    return [
        {"dock": dock["dock"], "seq_index": i,
         "sequence": f"SEQ{_rng.randint(0, 5)}", "mpnn_score": _rng.random()}
        for i in range(n_sequences)
    ]


async def score_designs(designs: list[dict], top_fraction: float = 0.75) -> dict:
    """Mean of the best fraction of ProteinMPNN scores for one dock.

    Matches CageFlow._group_score (CageFlow.py:1391): lower-is-better, so sort
    ascending and average the leading `ceil(n * top_fraction)`. Using int()
    instead of ceil() keeps one sequence too few on small docks.

    Recorded only. Laura: it "isn't used in any kind of selection or
    filtering", and ProteinMPNN scores are not strongly indicative. Never gate.
    """
    await _work("score_designs")
    ranked = sorted(d["mpnn_score"] for d in designs)
    keep = ranked[: max(1, math.ceil(len(ranked) * top_fraction))]
    return {"group_score": sum(keep) / len(keep), "n_sequences": len(designs)}


async def dedup_sequences(designs: list[dict], scope: str = "per_dock") -> list[dict]:
    """Drop candidate sequences that turned out identical.

    Two scopes, deliberately. `per_dock` is what Rosetta needs: a ddG depends
    on the sequence AND the interface it sits in, so the same sequence under
    two docks is two real calculations. `global` is what folding needs: a fold
    depends on the sequence alone. The global case holds only while docking
    moves a fixed scaffold.

    Args:
        designs: Candidate sequences from design_interface.
        scope: Either "per_dock" or "global".

    Returns:
        The surviving designs, order preserved.

    Raises:
        ValueError: If `scope` is neither.
    """
    if scope not in ("per_dock", "global"):
        raise ValueError(f"scope must be 'per_dock' or 'global', got {scope!r}")
    await _work("dedup_sequences")
    seen, kept = set(), []
    for d in designs:
        key = (d["dock"], d["sequence"]) if scope == "per_dock" else d["sequence"]
        if key not in seen:
            seen.add(key)
            kept.append(d)
    return kept


def _sigma(sequence: str) -> float:
    """This sequence's own Rosetta noise level, stable across calls.

    The real spread is a property of the sequence, not the step: some
    interfaces settle into the same packing every time and some do not. Seeding
    on the sequence means rescoring the same candidate draws from the same
    distribution, which is what makes replication meaningful. Most sequences
    are quiet; a minority are not.
    """
    seed = int(hashlib.md5(sequence.encode()).hexdigest()[:8], 16)
    return ROSETTA_NOISE_KCAL["high"] if seed % 4 == 0 else ROSETTA_NOISE_KCAL["low"]


async def score_with_rosetta(design: dict) -> dict:
    """Binding energy of the packed structure. The pipeline's only gate.

    The ddG carries run-to-run noise because PyRosetta's packer is unseeded,
    and the spread is per sequence rather than per step. The naive pipeline
    scores once and takes the number, as CageFlow does; modelling the noise
    here means the signal exists to act on later instead of being invented then.
    """
    await _work("score_with_rosetta")
    sigma = _sigma(design["sequence"])
    return {**design, "ddg": _rng.gauss(-_rng.uniform(4.0, 14.0), sigma),
            "ddg_noise_sigma": sigma}


async def fold_msa(sequence: str) -> str:
    """Build the multiple sequence alignment. CPU half of the fold branch."""
    await _work("fold_msa")
    return f"msa::{hashlib.md5(sequence.encode()).hexdigest()[:8]}"


async def fold_inference(sequence: str, msa: str) -> dict:
    """Predict the oligomer's structure. GPU half of the fold branch.

    Depends on the sequence alone, never the docked pose, which is what makes
    this branch independent of Rosetta and its results reusable across docks.
    """
    await _work("fold_inference")
    return {"sequence": sequence, "plddt": _rng.uniform(0.5, 0.95),
            "iptm": _rng.uniform(0.4, 0.9), "rmsd": _rng.uniform(0.5, 4.0)}


# relax_qualifying (step 10) is deliberately absent. Laura disabled it as too
# expensive and unpredictable, and CageFlow raises on configs that still ask
# for it. STAGES keeps it with enabled=False so nothing can schedule it.


#: Stand-in for the folding binary. Kept as a separate process because that is
#: how the real one has to be called.
FOLD_SCRIPT = Path(__file__).resolve().parent / "fold_stub.py"


def fold_command(sequence: str, msa_handle: str) -> str:
    """The command line that folds one sequence."""
    seconds = (STAGES["fold_inference"].cost_s or UNMEASURED_S) / SPEEDUP
    return shlex.join([sys.executable, str(FOLD_SCRIPT),
                       "--sequence", sequence, "--msa", msa_handle,
                       "--seconds", f"{seconds:.4f}"])
