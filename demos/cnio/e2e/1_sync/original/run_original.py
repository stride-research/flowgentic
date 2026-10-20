"""Track A reference run: her unmodified `run_cage_flow()` entry point, called directly.

Runs under the self-contained venv (no `module load`, so PYTHONPATH is never set -- see
context/current-implementation.md "UPDATE 2026-10-20"), which is why no PYTHONPATH scrubbing
exists here. Zero changes to CageFlow.py or fold.py either way.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/javidsegura/software/CageFlow")
import CageFlow  # noqa: E402

CONFIG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "config.json"


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
