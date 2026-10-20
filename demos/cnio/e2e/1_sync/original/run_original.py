"""Track A reference run: her unmodified `run_cage_flow()` entry point, called directly.

Replaces an earlier version of this file that reproduced `_run_cage_flow_steps()`'s order by
hand so a `PYTHONPATH` pop could be inserted between score_with_rosetta and fold_oligomer (see
git history for that version). That approach worked, but every line of it was a parallel copy of
orchestration logic that already lives in CageFlow.py -- reviewable only by re-deriving that it
matches her internals, and silently stale the moment she changes `_run_cage_flow_steps()`.

The actual bug was never in orchestration order. It is in fold.py's two `subprocess.run(cmd,
capture_output=True, text=True, timeout=...)` calls (fetch_msa, fold_sequence): neither passes
`env=`, so both inherit this process's full environment, including `PYTHONPATH`. Our cluster venv
needs `PYTHONPATH` set for ProteinMPNN's/Rosetta's subprocess imports (bare `numpy` otherwise
fails -- confirmed: `env -u PYTHONPATH cageflow_unified/bin/python3 -c "import numpy"` fails
outright), but ColabFold's bundled python3.10 breaks when it inherits that same `PYTHONPATH`.
Calling her unmodified CLI (`python3 CageFlow.py config.json` -> `run_cage_flow(config)`) hits
this exact crash (job 24016, 2026-10-18): design_interface()'s ProteinMPNN subprocess died with
`ModuleNotFoundError: No module named 'numpy'`, not fold -- because the earlier wrapper popped
PYTHONPATH right after `import CageFlow`, before any stage ran.

The fix here is scoped to the two calls that actually need it, not the whole process: monkeypatch
`fold.subprocess.run` (the module-level reference fold.py's own calls use), not the global
`subprocess.run`, so only fold.py's two subprocess calls lose PYTHONPATH -- every other stage
(design_interface's ProteinMPNN, score_with_rosetta's PyRosetta) keeps it, untouched.

Zero changes to CageFlow.py or fold.py. `run_cage_flow()` below is her exact, unmodified top-level
function -- the same thing `python3 CageFlow.py config.json` calls.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/home/javidsegura/software/CageFlow")
import CageFlow  # noqa: E402
import fold  # noqa: E402 -- same module object CageFlow.py's lazy `from fold import ...` reuses

CONFIG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "config.json"

_real_subprocess_run = subprocess.run

def _run_without_pythonpath(cmd, **kwargs):
    """fold.py's subprocess.run, scoped to drop PYTHONPATH -- see module docstring."""
    if "env" not in kwargs:
        kwargs["env"] = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return _real_subprocess_run(cmd, **kwargs)


fold.subprocess.run = _run_without_pythonpath


def main():
    config = json.loads(CONFIG_PATH.read_text())
    out_dir = Path(config.get("out_dir", "cageflow_output"))
    out_dir.mkdir(parents=True, exist_ok=True)

    result = CageFlow.run_cage_flow(config)

    n_designed = sum(len(d) for d in result["designed_oligomers"].values())
    n_folded = sum(len(f) for f in result["fold_results"].values())
    print(f"\nTrack A reference run complete: {n_designed} sequence(s) designed, "
          f"{n_folded} fold(s) attempted. See {out_dir}/cageflow.log for full detail.")


if __name__ == "__main__":
    main()
