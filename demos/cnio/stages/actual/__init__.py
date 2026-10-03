"""The real CageFlow steps. None are wired up yet.

Same names and signatures as `stages.simulated`, so swapping backend is the
only change. Each raises with what is actually missing rather than a bare
NotImplementedError, because that is the question asked on every call.

Every real step is a subprocess, never an import: ProteinMPNN, PyRosetta and
the folding tool each need their own environment and they conflict.

CageFlow has no per-stage CLI, only `python CageFlow.py config.json`, which
runs everything. But its steps are plain module-level functions taking a config
dict, so calling one alone just means importing the module and passing the
right arguments.
"""

from __future__ import annotations

from .. import STAGES

#: What each step still needs. A step leaves this list only once it has
#: actually run, not once we think it would.
MISSING: dict[str, str] = {
    "dock_oligomer":
        "ProteinBuilder (github.com/irikosga/ProteinBuilder, branch LW_WT, MIT) on sys.path "
        "as config['cage_designer_path'], plus a scaffold PDB. Cloned locally; not on the "
        "cluster yet.",
    "dock_two_component":
        "same ProteinBuilder dependency as dock_oligomer, plus a second scaffold "
        "(config['scaffold_pdb_b']).",
    "evaluate_geometry":
        "same ProteinBuilder dependency as dock_oligomer.",
    "select_top_docks":
        "nothing external; pure ranking over evaluate_geometry's output.",
    "dedup_sequences":
        "no CageFlow implementation exists to call - ours is the only one. CageFlow "
        "dedups only inside the fold path.",
    "design_interface":
        "ProteinMPNN at ~/software/ProteinMPNN on the cluster (installed). Its torch comes from "
        "a container, so the subprocess must go through `torch-python`, not sys.executable.",
    "score_designs":
        "nothing external; needs design_interface's output.",
    "score_with_rosetta":
        "PyRosetta (installed, ~/venvs/cageflow, 2026.29) plus ProteinBuilder for src.pdb_utils.",
    "fold_msa":
        "OpenFold is installed at IE and runs its own MSA locally, so CageFlow's ColabFold-based "
        "MSA step needs rewriting rather than installing.",
    "fold_inference":
        "OpenFold is installed (module openfold/be2ec18, databases staged) but CageFlow calls "
        "ColabFold. Adapt to the openfold-predict wrapper. Fold numbers will then not be "
        "directly comparable with CNIO's.",
}


def _blocked(stage: str):
    """Raise with what is missing for this step.

    Raises:
        NotImplementedError: Always.
    """
    spec = STAGES[stage]
    raise NotImplementedError(
        f"{MISSING.get(stage, 'unknown')}  (real implementation: "
        f"{spec.source or 'none in CageFlow'})"
    )


async def dock_oligomer(index: int, radius: float, spin: float) -> dict:
    """Place the scaffold at one point on the docking grid."""
    _blocked("dock_oligomer")


async def dock_two_component(index: int, radius_a: float, radius_b: float,
                             spin_a: float, spin_b: float) -> dict:
    """Place two different building blocks at one point on the joint grid."""
    _blocked("dock_two_component")


async def evaluate_geometry(dock: dict, min_contacts: int = 3) -> dict:
    """Score a pose on clashes and interface contacts."""
    _blocked("evaluate_geometry")


async def select_top_docks(docked_oligomers: list[dict], geometries: list[dict],
                           n_docks: int) -> list[int]:
    """Rank the arrangements and keep the best few."""
    _blocked("select_top_docks")


async def dedup_sequences(designs: list[dict], scope: str = "per_dock") -> list[dict]:
    """Drop candidate sequences that turned out identical."""
    _blocked("dedup_sequences")


async def design_interface(dock: dict, n_sequences: int) -> list[dict]:
    """Redesign the interface residues into several candidate sequences."""
    _blocked("design_interface")


async def score_designs(designs: list[dict], top_fraction: float = 0.75) -> dict:
    """Average the best ProteinMPNN scores for one dock."""
    _blocked("score_designs")


async def score_with_rosetta(design: dict) -> dict:
    """Binding energy of the packed structure. The pipeline's only gate."""
    _blocked("score_with_rosetta")


async def fold_msa(sequence: str) -> str:
    """Build the multiple sequence alignment."""
    _blocked("fold_msa")


async def fold_inference(sequence: str, msa: str) -> dict:
    """Predict the oligomer's structure."""
    _blocked("fold_inference")


def fold_command(sequence: str, msa_handle: str) -> str:
    """The command line that folds one sequence."""
    _blocked("fold_inference")
