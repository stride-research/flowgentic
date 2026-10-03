"""The CageFlow pipeline, one entry per step.

Laura's filled-in operation map turned into data, cross-checked against her
`integrating_agent_demo` branch at `eb6113d`.

Costs are her measurements (run 9759675). Where a step was never timed, the
cost is None rather than a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Hardware(str, Enum):
    """Which pool a step can run on. EITHER means a real runtime choice."""

    CPU = "cpu"
    GPU = "gpu"
    EITHER = "either"


@dataclass(frozen=True)
class Stage:
    """One step in the pipeline.

    Attributes:
        number: Row number in Laura's operation map, so the two line up.
        summary: What it does, in the domain scientist's words.
        hardware: Pool it runs on.
        depends_on: Steps whose output it consumes.
        source: Where the real implementation lives in the CageFlow repo.
        check: How we know a standalone run worked. Laura filled this in.
        cost_s: Measured seconds per task, or None if never timed.
        enabled: False for steps that exist but are deliberately not run.
        note: Only what changes a decision.
    """

    number: str
    summary: str
    hardware: Hardware
    depends_on: tuple[str, ...]
    source: str | None
    check: str
    cost_s: float | None
    enabled: bool = True
    note: str = ""


STAGES: dict[str, Stage] = {
    "dock_oligomer": Stage(
        number="1",
        summary="Try different arrangements of the cage pieces and build the 3D shape of each.",
        hardware=Hardware.CPU,
        depends_on=(),
        source="CageFlow.py:788",
        check="history.jsonl has the radius and spin of each dock, one line per dock.",
        cost_s=0.19,
        note="Our grid is a plain radius x spin product; the real one dropped the redundant "
             "spin sweep and added flip and a radius window. Ask Laura what it enumerates now.",
    ),
    "dock_two_component": Stage(
        number="1b",
        summary="Same, for cages built from two different blocks, coordinated at once.",
        hardware=Hardware.CPU,
        depends_on=(),
        source="CageFlow.py:592",
        check="Same as dock_oligomer. Selected by setting `scaffold_pdb_b` in the config.",
        cost_s=None,
        note="Actively changing under Laura. Two-component docks now DO get Rosetta scoring "
             "(via score_two_component_assembly) but are still skipped by folding.",
    ),
    "evaluate_geometry": Stage(
        number="2",
        summary="Cheap check per arrangement: do the pieces overlap, how many contacts?",
        hardware=Hardware.CPU,
        depends_on=("dock_oligomer",),
        source="CageFlow.py:1013",
        check="history.jsonl records geometry passed or failed per dock.",
        cost_s=0.042,
    ),
    "select_top_docks": Stage(
        number="3",
        summary="Rank the arrangements and keep the best few.",
        hardware=Hardware.CPU,
        depends_on=("evaluate_geometry",),
        source="CageFlow.py:1076",
        check="A PDB per kept dock; history.jsonl says selected or not_selected.",
        cost_s=None,
        note="The pipeline's only hard barrier: it cannot rank until every dock is in.",
    ),
    "build_mpnn_config": Stage(
        number="4",
        summary="Work out which residues sit at the interface, in ProteinMPNN's format.",
        hardware=Hardware.CPU,
        depends_on=("select_top_docks",),
        source="CageFlow.py:1159",
        check="One config per passing dock, carrying the residues of interest.",
        cost_s=None,
        enabled=False,
        note="Not a separate step. Runs inside design_interface; Laura said to drop the row. "
             "Kept only so the numbering matches her map.",
    ),
    "design_interface": Stage(
        number="5",
        summary="Generate candidate sequences for the interface residues. The fan-out: "
                "one pose becomes many sequences.",
        hardware=Hardware.EITHER,
        depends_on=("select_top_docks",),
        source="CageFlow.py:1331 -> run_mpnn.MPNNRunner",
        check="n sequences per dock in one FASTA, identity lines carrying score metrics.",
        cost_s=43.8,
        note="The only step with a real hardware choice: runs on both, faster on GPU. "
             "Must be a subprocess; ProteinMPNN's env conflicts with CageFlow's.",
    ),
    "score_designs": Stage(
        number="6",
        summary="Score each candidate sequence and average the best per arrangement.",
        hardware=Hardware.CPU,
        depends_on=("design_interface",),
        source="CageFlow.py:1409",
        check="Scores appear in history.jsonl.",
        cost_s=None,
        note="Recorded only. Laura: it 'isn't used in any kind of selection or filtering'. "
             "Do not gate on it.",
    ),
    "dedup_sequences": Stage(
        number="7",
        summary="Drop candidate sequences that turned out identical.",
        hardware=Hardware.CPU,
        depends_on=("score_designs",),
        source=None,
        check="Top sequences decrease in number or stay the same.",
        cost_s=None,
        note="Two scopes, deliberately. Rosetta dedups PER DOCK (a ddG depends on the sequence "
             "AND its interface). Folding dedups GLOBALLY (a fold depends on the sequence alone). "
             "The global case holds only while docking moves a fixed scaffold.",
    ),
    "score_with_rosetta": Stage(
        number="8",
        summary="The heavy pass: binding energy (ddG) for each surviving sequence.",
        hardware=Hardware.CPU,
        depends_on=("dedup_sequences",),
        source="optimize/real_rosetta.py",
        check="Scores appear in history.jsonl.",
        cost_s=134.3,
        note="THE ONLY GATE IN THE PIPELINE. Structure-based, so it must follow ProteinMPNN. "
             "Cost drifted 33% between runs on identical input; size walltime generously.",
    ),
    "fold_msa": Stage(
        number="9a",
        summary="Build the multiple sequence alignment for a designed sequence.",
        hardware=Hardware.CPU,
        depends_on=("dedup_sequences",),
        source="colabfold_msa.py",
        check="An .a3m file with the right complex header (e.g. '#201\\t3' for a 201-residue trimer).",
        cost_s=12.2,
        note="Split off the GPU because 40 of a fold's 110 s were spent waiting on "
             "api.colabfold.com while holding an A100. At IE we use OpenFold, which does its "
             "own MSA locally, so check whether this split still buys anything here.",
    ),
    "fold_inference": Stage(
        number="9b",
        summary="Predict the structure of the designed sequence and compare it to the docked pose.",
        hardware=Hardware.GPU,
        depends_on=("fold_msa",),
        source="fold.py -> colabfold_batch",
        check="pLDDT, ipTM and RMSD written to history.jsonl. Recorded, never gated on.",
        cost_s=79.4,
        note="Opt-in, top N per dock (default 1). Ranked by ddG where it exists, falling back "
             "to the ProteinMPNN score, so it needs nothing from Rosetta. Needs ~13.5 GB.",
    ),
    "relax_qualifying": Stage(
        number="10",
        summary="A second, heavier re-scoring pass over sequences that passed the gate.",
        hardware=Hardware.CPU,
        depends_on=("score_with_rosetta",),
        source="relax_qualifying.py",
        check="Writes its own CSV. Never touches history.jsonl.",
        cost_s=None,
        enabled=False,
        note="DISABLED. Laura: 'NO LONGER RELAXING FOR NOW - TOO EXPENSIVE AND UNPREDICTABLE'. "
             "CageFlow now raises on configs that still ask for it. Carried, commented out.",
    ),
}


#: Rosetta's run-to-run noise, measured over 8 replicates of one sequence.
#:
#: The point is not the size but the spread: 0.092 kcal/mol for one sequence and
#: 1.055 for another in the same run, 11x apart. For the noisy one the 2-sigma
#: band is wider than the gap between the docks being compared, so one scoring
#: run cannot reliably rank designs. Laura's conclusion: replicate adaptively,
#: not uniformly.
ROSETTA_NOISE_KCAL = {"low": 0.092, "high": 1.055}
