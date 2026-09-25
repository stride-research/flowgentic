"""Stand-in implementations of CageFlow's stages.

Each function here is a placeholder for one real scientific step, and is
written to be deleted. When CNIO confirms the command or function behind a
stage, its mock body is replaced and nothing else in the pipeline changes --
the data flow, the dependency graph and the fan-out all stay as they are.

Durations come from CageFlow's own committed telemetry where a measurement
exists, and from what CNIO said on the calls where one does not. Unmeasured
figures are marked, because an invented cost driving a scheduling decision
should be visible every time it is read.

They are scaled by SPEEDUP so a demonstration finishes in seconds. Set it to
1.0 for true-to-life timings.
"""

import asyncio
import hashlib
import random

#: Divides every duration. 1.0 runs at real speed (hours).
SPEEDUP = 400.0

#: Seconds per task. MEASURED unless marked otherwise.
COST = {
    "dock_oligomer": 0.16,        # measured, mean of 24 tasks over two runs
    "evaluate_geometry": 0.04,    # measured
    "design_interface": 54.7,     # measured on GPU
    "relax": 1200.0,              # CNIO: "~20 min/sequence" -- NOT MEASURED
    "score_with_rosetta": 101.9,  # measured
    "fetch_msa": 900.0,           # CNIO: "~15 minutes" -- NOT MEASURED
    "fold_inference": 60.0,       # CNIO: "about a minute" -- NOT MEASURED
}

#: Run-to-run spread for stages that are genuinely stochastic, in the units
#: each stage reports. Scoring the same candidate twice does not give the same
#: answer, and a mock that pretends otherwise hides the one property that
#: makes replication worth doing later.
#:
#: **These sigmas are asserted, not measured.** The 0.3 kcal/mol for Rosetta
#: comes from an illustrative figure in our own architecture sketch, never
#: from a measurement. CNIO now ships `measure_rosetta_noise.py` and seeds the
#: packer (`rosetta_seed`), so the real number is obtainable -- and a seeded
#: packer may mean the real spread is near zero, which would change the
#: argument for replication entirely. Replace these before any figure derived
#: from them appears in a document.
NOISE = {
    "score_with_rosetta": 0.3,   # kcal/mol, ASSERTED
    "fold_inference": 0.02,      # pLDDT units, ASSERTED
}

_rng = random.Random(7)


async def _work(stage: str) -> None:
    """Occupy a worker for roughly what the real stage costs."""
    await asyncio.sleep(COST[stage] / SPEEDUP)


async def dock_oligomer(index: int, radius: float, spin: float) -> dict:
    """Place the scaffold at one point on the docking grid."""
    await _work("dock_oligomer")
    return {"dock": index, "radius": radius, "spin": spin}


async def evaluate_geometry(dock: dict) -> dict:
    """Score a pose on clashes and interface contacts."""
    await _work("evaluate_geometry")
    return {**dock, "contacts": _rng.randint(0, 20), "clashes": _rng.randint(0, 3)}


async def design_interface(dock: dict, n_sequences: int) -> list[dict]:
    """Redesign the interface residues, producing several candidate sequences.

    Returns a list because this is the pipeline's fan-out point: one pose
    yields many sequences, and everything downstream is per-sequence.
    """
    await _work("design_interface")
    return [
        {
            "dock": dock["dock"],
            "seq_index": i,
            # Deliberately drawn from a small alphabet so duplicates occur, as
            # they do in practice: ProteinMPNN samples at a low temperature to
            # stay close to the native sequence, so two runs often coincide.
            "sequence": f"SEQ{_rng.randint(0, 5)}",
            "mpnn_score": _rng.random(),
        }
        for i in range(n_sequences)
    ]


async def relax(design: dict) -> dict:
    """Relax the structure before scoring. Rosetta branch."""
    await _work("relax")
    return {**design, "relaxed": True}


async def score_with_rosetta(relaxed: dict) -> dict:
    """Binding energy of the relaxed structure. Rosetta branch, and the gate.

    The returned ddG carries run-to-run noise: PyRosetta's packer is
    stochastic, so the same sequence on the same structure scores differently
    each time. Two candidates separated by less than that spread are not
    actually distinguishable, which is the entire basis for replicating a
    measurement instead of exploring a new candidate.

    Detecting and acting on that is a later concern -- the naive pipeline
    scores once and takes the number. Modelling it here means the signal
    exists to be acted on when the time comes, rather than being invented.
    """
    await _work("score_with_rosetta")
    true_ddg = -_rng.uniform(4.0, 14.0)
    observed = _rng.gauss(true_ddg, NOISE["score_with_rosetta"])
    return {**relaxed, "ddg": observed, "ddg_noise_sigma": NOISE["score_with_rosetta"]}


async def fetch_msa(sequence: str) -> str:
    """Build the multiple sequence alignment. Fold branch.

    Expensive and CPU-bound, and the reason the fold branch is split in two:
    CNIO cache this aggressively, plausibly computing one alignment for an
    entire campaign since interface redesign barely shifts the sequence.
    """
    await _work("fetch_msa")
    return f"msa::{hashlib.md5(sequence.encode()).hexdigest()[:8]}"


async def fold_inference(sequence: str, msa: str) -> dict:
    """Predict the oligomer's structure. Fold branch.

    Depends on the sequence alone -- it never sees the docked pose -- which is
    what makes this branch independent of relax and Rosetta, and what makes
    its results reusable across docks that happen to share a sequence.
    """
    await _work("fold_inference")
    return {"sequence": sequence, "plddt": _rng.uniform(0.5, 0.95),
            "iptm": _rng.uniform(0.4, 0.9), "rmsd": _rng.uniform(0.5, 4.0)}


# --- real executables ------------------------------------------------------

import shlex
import sys
from pathlib import Path

#: Stand-in for ColabFold. Swap this path and its flags for the real binary.
FOLD_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "fold_stub.py"


def fold_command(sequence: str, msa_handle: str) -> str:
    """The command line that folds one sequence.

    Quoted with shlex because AsyncFlow splits the string into argv, and real
    inputs (file paths, sequence ids) can contain spaces even when these
    stand-ins do not.
    """
    seconds = COST["fold_inference"] / SPEEDUP
    return shlex.join([
        sys.executable, str(FOLD_SCRIPT),
        "--sequence", sequence,
        "--msa", msa_handle,
        "--seconds", f"{seconds:.4f}",
    ])
