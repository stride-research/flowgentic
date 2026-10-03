"""Shared helpers for CageFlow's per-step isolation scripts.

Every script here runs standalone, on the IE cluster, via srun:

    real input -> call CageFlow's real function, unmodified -> write
    the artifact -> assert Laura's own correctness criterion.

No AsyncFlow, no rhapsody, no Executor abstraction beyond her own
SequentialExecutor -- that layer is a separate, later piece of work
(see CLAUDE.md). This module exists only to avoid repeating path setup
and artifact bookkeeping in every one of those scripts.

Cluster paths below are hardcoded, not discovered, because these scripts
exist to run in exactly one place: the IE cluster, never locally.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HOME = Path.home()
CAGEFLOW_DIR = HOME / "software" / "CageFlow"
CAGE_DESIGNER_PATH = str(HOME / "software" / "ProteinBuilder")
MPNN_DIR = str(HOME / "software" / "ProteinMPNN")
COLABFOLD_DIR = HOME / "software" / "localcolabfold_install" / "localcolabfold"
COLABFOLD_BIN = str(COLABFOLD_DIR / "colabfold-conda" / "bin" / "colabfold_batch")
COLABFOLD_DATA = str(COLABFOLD_DIR / "colabfold")

sys.path.insert(0, str(CAGEFLOW_DIR))
import CageFlow  # noqa: E402  (path must be set before this import)

#: Shared scratch dir for things CageFlow itself writes (e.g. the
#: component_preparation.json report). NOT where artifact.json goes --
#: each step writes that beside its own main.py, see write_artifact().
SCRATCH_DIR = Path(__file__).resolve().parent / "_scratch"


#: config.1c5e_noflip.json is the literal config Laura pointed to when she
#: pushed 1c5e_trimer.pdb (b24916b touches exactly these two files). It is
#: full-scale: num_seq_per_target=100, fold_oligomers=true -- too expensive
#: for a per-step isolation check. config.1c5e_probe.json is a DIFFERENT,
#: pre-existing, lighter sibling in her repo (num_seq_per_target=4,
#: fold_oligomers=false) that we chose ourselves for isolation, verified to
#: NOT reduce the actual docking search (its dock_ranges are finer, not
#: coarser, than the full-scale config's). Use the noflip one explicitly
#: once we move past per-step isolation to the real reference run.
DEFAULT_BASE_CONFIG = "config.1c5e_probe.json"


def load_config(base: str = DEFAULT_BASE_CONFIG, **overrides) -> dict:
    """One of Laura's own configs, repointed at our cluster clones.

    Starting from HER config rather than a hand-built dict means every
    key we don't explicitly override still matches what she actually
    runs -- the whole point of isolation is testing her pipeline, not a
    reimplementation of its inputs.

    Args:
        base: filename under CageFlow's configs/ to start from. Defaults
            to the lightweight probe config; pass e.g.
            "config.1c5e_noflip.json" for the full-scale one she handed us.
        **overrides: Keys to override on top of that base config (e.g.
            max_materialized=1 to stop a stage after one real candidate).

    Returns:
        The merged config dict, with scaffold_pdb and out_dir made
        absolute so the script's behavior doesn't depend on cwd.
    """
    base = json.loads((CAGEFLOW_DIR / "configs" / base).read_text())
    base["cage_designer_path"] = CAGE_DESIGNER_PATH
    base["mpnn_dir"] = MPNN_DIR
    base["colabfold_bin"] = COLABFOLD_BIN
    base["colabfold_data"] = COLABFOLD_DATA
    base["executor"] = "sequential"
    base["scaffold_pdb"] = str(CAGEFLOW_DIR / base["scaffold_pdb"])
    base["out_dir"] = str(SCRATCH_DIR / "cageflow_out")
    base.update(overrides)
    return base


def write_artifact(caller_file: str, data) -> Path:
    """Write artifact.json beside the calling step's main.py.

    Every step's evidence lives next to its own script and SUCCESS.md --
    one folder per step holds everything about it: how to run it, what
    passing looks like, and what actually happened last time it ran.

    Args:
        caller_file: pass `__file__` from the main.py calling this.
        data: JSON-serializable result to record.
    """
    path = Path(caller_file).resolve().parent / "artifact.json"
    path.write_text(json.dumps(data, indent=2, default=str))
    return path


def check(label: str, condition: bool, detail: str = "") -> None:
    """Print PASS/FAIL and raise on failure, so a failing srun job exits nonzero.

    Raises:
        AssertionError: If `condition` is False.
    """
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f"  -- {detail}" if detail else ""))
    if not condition:
        raise AssertionError(f"{label}: {detail}")
