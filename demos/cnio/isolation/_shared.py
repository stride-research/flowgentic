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

## One environment, every step

Every `run.sh` does the identical two lines before running `main.py`:

    module load PyTorch/2.1.2-foss-2023a
    source ~/venvs/cageflow_unified/bin/activate

`module load` alone gives a working numpy/pandas/torch (needed because
CageFlow.py imports pandas transitively via history_table.py). The venv
on top adds PyRosetta -- `python3 -m venv --system-site-packages` seeded
from that module-loaded interpreter, then `pip install pyrosetta`. Before
this venv existed, PyRosetta only lived in a separate, incompatible venv
(broken numpy, wrong CPU SIMD baseline), which forced score_with_rosetta's
isolation step onto a different Python than everyone else and made it
skip CageFlow.py/config entirely. One venv, used by every step whether it
needs PyRosetta or not, means there is exactly one environment-setup
incantation in this whole folder, not two.

There is no such thing as a "step-only" config in CageFlow. Every stage
function -- dock_oligomer, evaluate_geometry, select_top_docks,
design_interface, score_with_rosetta, fold_oligomer -- takes a parameter
literally named `config`, and all of them receive the SAME dict. Each one
just reads the handful of keys it cares about via config.get(...) and
ignores the rest. So a step-scoped config doesn't exist to use instead --
this full shape is the only input contract her real functions accept.

That also means hand-picking "just the keys this step needs" is riskier
than it looks: dock_oligomer's own body reads 18 keys, but it ALSO calls
three helpers defined earlier in the file (_spin_period,
_resolve_radius_window, _prepare_component) that read 9 more
(idealise, idealise_ref_chain, max_fidelity_rmsd, radius_search_max,
spin_period_max_closure, use_spin_period, clash_dist, contact_dist,
dock_ranges) -- easy to miss on a shallow read of just the one function.
Using her real, complete, validated config sidesteps that risk entirely.

## Config reference

**Everything below explains the VALUES in `config.1c5e_probe.json` specifically** (our
isolation default -- see DEFAULT_BASE_CONFIG). The key *names* and what each one means are the
same across any CageFlow config; only the values differ. If `load_config(base=...)` is ever
pointed at a different file (e.g. her real `config.1c5e_noflip.json`), the key meanings below
still apply, but re-check the actual values in that file -- don't assume they match what's
written here.

Keys WE override in load_config(), and why:

    cage_designer_path   her CNIO path -> our cluster's ProteinBuilder clone
    mpnn_dir             her CNIO path -> our cluster's ProteinMPNN clone
    colabfold_bin/_data/_python  unset in probe -> our installed localcolabfold
    scaffold_pdb         made absolute (CageFlow reads it relative to cwd)
    out_dir              made absolute, under _scratch/

We do NOT override `executor` -- every config already specifies its own (probe already says
"sequential"; forcing it here would silently undo that choice if `base=` is ever pointed at a
config that picks something else, like her full-scale config's "process").

Every other key below comes from HER file, unchanged -- these are not our
settings, they are what she considers a reasonable small sanity run, and
the VALUES shown are config.1c5e_probe.json's specifically:

    scaffold_pdb          1c5e_trimer.pdb -- her Oct 2 trimer, 3 identical
                          95-residue chains
    symmetry              "T" (tetrahedral) -- how the 3 copies assemble
    dock_ranges           {radius_step: 0.25, spin_step: 1.0} -- the
                          docking grid's step sizes. FINER than her
                          full-scale config's 0.5, not coarser.
    n_docks               5 -- how many top docks select_top_docks keeps
    clash_dist            3.0 A -- how close atoms can get before "clash"
    contact_dist          12.0 A -- how close counts as "in contact"
    min_contacts          3 -- minimum contacts for a dock to pass geometry
    num_seq_per_target    4 -- ProteinMPNN sequences generated per dock
                          (100 in her full-scale config -- this is the
                          main thing that makes probe cheap)
    rosetta_score_cutoff  2.0 -- the ddG gate: below this, a design passes
    rosetta_chi_min       true -- cheap side-chain repacking before scoring
    rosetta_seed          a fixed seed, for reproducibility
    fold_oligomers        false in probe -- fold is opt-in; step 7's
                          isolation overrides this to true explicitly
    fold_top_n            1 -- how many sequences per dock get folded
    fold_msa_mode         "mmseqs2_uniref_env" -- ColabFold's MSA mode
    fold_model_type       "alphafold2_multimer_v3"
    fold_num_models       1, fold_num_recycle 3, fold_seed, fold_timeout_s
                          -- ColabFold inference settings
    flip                  true -- try the flipped placement too
    select_by             "cbeta" -- how select_top_docks ranks candidates
    mpnn_force_cpu        true -- run ProteinMPNN on CPU, not GPU
    thread_mode           "replace" -- how a designed sequence is threaded
                          onto the structure before Rosetta scoring
    max_materialized      50 in her file; we further override this
                          per-script (e.g. 1 for step 1's isolation) --
                          see that step's own SUCCESS.md for why that is
                          safe and does not reduce the search itself
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
COLABFOLD_PYTHON = str(COLABFOLD_DIR / "colabfold-conda" / "bin" / "python")

sys.path.insert(0, str(CAGEFLOW_DIR))
import CageFlow  # noqa: E402  (path must be set before this import)


DEFAULT_BASE_CONFIG = "config.1c5e_probe.json"


def load_config(caller_file: str, base: str = DEFAULT_BASE_CONFIG, **overrides) -> dict:
    """One of Laura's own configs, repointed at our cluster clones.

    Starting from HER config rather than a hand-built dict means every
    key we don't explicitly override still matches what she actually
    runs -- the whole point of isolation is testing her pipeline, not a
    reimplementation of its inputs.

    `out_dir` is derived from `caller_file`, giving every step its OWN
    on-disk scratch space (`<step folder>/_scratch/cageflow_out`). Earlier
    versions pointed every step at one shared directory, so running step 5
    silently overwrote step 4's on-disk files for the same dock indices --
    each script still produced a correct result on its own, but one
    step's run could corrupt another's leftover artifacts. One real input
    per caller_file keeps steps independent on disk, matching
    write_artifact()'s existing pattern.

    Args:
        caller_file: pass `__file__` from the calling main.py.
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
    base["colabfold_python"] = COLABFOLD_PYTHON
    base["scaffold_pdb"] = str(CAGEFLOW_DIR / base["scaffold_pdb"])
    base["out_dir"] = str(Path(caller_file).resolve().parent / "_scratch" / "cageflow_out")
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
