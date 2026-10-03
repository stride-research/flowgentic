"""Run one pipeline step on its own.

    python -m demos.cnio.stages.actual.driver              # list the steps
    python -m demos.cnio.stages.actual.driver dock_oligomer
    python -m demos.cnio.stages.actual.driver dock_oligomer --real

Each step gets a fixture input, so no step needs its predecessor to have run.
That is what makes stepping through them one at a time possible.

Without `--real` it calls the stand-in, which needs none of the scientific
dependencies and still checks that the step is wired up and returns the right
shape. With `--real` it calls CageFlow, and failures say what is missing.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import socket
from pathlib import Path

from .. import STAGES

#: Where a run's output is written, one file per step, so results can be
#: inspected and handed over without re-running anything.
OUT_DIR = Path(__file__).resolve().parents[1] / "runs" 

#: Minimal correctly-shaped input per step. If a real step needs more than
#: this, that gap is worth writing down.
FIXTURES: dict[str, dict] = {
    "dock_oligomer": {"index": 0, "radius": 40.0, "spin": 60.0},
    "dock_two_component": {"index": 0, "radius_a": 40.0, "radius_b": 47.0,
                           "spin_a": 60.0, "spin_b": 30.0},
    "evaluate_geometry": {
        "dock": {"dock_index": 0, "solution": {"spin": 60.0, "lat": 0.0, "lon": 0.0,
                                               "radius": 40.0, "score": 15.2, "contacts": 45,
                                               "min_clash": 2.96, "helix_contacts": 0}},
    },
    "select_top_docks": {
        "docked_oligomers": [{"dock_index": i, "solution": {"score": s}}
                             for i, s in enumerate([15.2, 4.1, 18.9, 11.0])],
        "geometries": [{"passed": p} for p in (True, True, False, True)],
        "n_docks": 2,
    },
    "design_interface": {
        "dock": {"dock": 0, "radius": 40.0, "spin": 60.0, "contacts": 12, "clashes": 0},
        "n_sequences": 3,
    },
    "score_designs": {
        "designs": [{"dock": 0, "seq_index": i, "sequence": f"SEQ{i}", "mpnn_score": 0.4 + i / 10}
                    for i in range(3)],
    },
    "dedup_sequences": {
        "designs": [{"dock": 0, "sequence": "SEQ1"}, {"dock": 0, "sequence": "SEQ1"},
                    {"dock": 1, "sequence": "SEQ1"}, {"dock": 0, "sequence": "SEQ2"}],
        "scope": "per_dock",
    },
    "score_with_rosetta": {
        "design": {"dock": 0, "seq_index": 0, "sequence": "SEQ0", "mpnn_score": 0.42},
    },
    "fold_msa": {"sequence": "SEQ0"},
    "fold_inference": {"sequence": "SEQ0", "msa": "msa::deadbeef"},
}

#: CNIO's own run output. The contract each step must satisfy is read from
#: here rather than written by us, so a step cannot pass by agreeing with an
#: expectation we invented.
REAL_HISTORY = Path(__file__).resolve().parents[2] / "reference" / "laura_history.jsonl"


def _contract_from_real_run() -> dict[str, tuple[str, ...]]:
    """Derive each step's required output keys from CNIO's real history.jsonl.

    A record there is one dock at one stage: `solution` is what docking
    produced, `geometry` what the geometry check produced, `design` what the
    design and scoring steps produced. Taking the keys from that file means
    the test fails when our shape drifts from theirs, which is the only
    failure mode worth catching before the real implementations land.

    Returns:
        Step name -> keys its output must contain. Steps with no counterpart
        in the file are absent and fall back to `ASSERTED` below.
    """
    rows = [json.loads(line) for line in REAL_HISTORY.read_text().splitlines() if line.strip()]
    by_status = {r["status"]: r for r in rows}

    # Each step is described by the record written at the status it produces.
    # The `design` block is NOT one shape: it grows as a dock progresses, so
    # taking the first record with a `design` key gives the wrong contract.
    sources = {
        "dock_oligomer": ("designed", "solution"),
        "evaluate_geometry": ("geometry_failed", "geometry"),
        "design_interface": ("designed", "design"),
        "score_designs": ("scored", "design"),
        "score_with_rosetta": ("rosetta_scored", "design"),
        "fold_inference": ("folded", "design"),
    }
    contract: dict[str, tuple[str, ...]] = {}
    for stage, (status, block) in sources.items():
        row = by_status.get(status)
        if row and row.get(block):
            contract[stage] = tuple(sorted(row[block]))
    return contract


#: Steps CNIO's history does not describe, so these are ours and are marked as
#: such. They are assertions about our own stand-ins and prove correspondingly
#: less.
ASSERTED: dict[str, tuple[str, ...]] = {
    "dock_two_component": ("dock_index", "mode", "solution"),
}

EXPECTED = {**ASSERTED, **_contract_from_real_run()}

#: Steps whose required keys sit one level down, under "solution".
NESTED = {"dock_oligomer": "solution"}


def verify(stage: str, output) -> str | None:
    """Return a failure reason, or None if the output looks right."""
    if output is None:
        return "returned None"
    if stage in ("design_interface", "select_top_docks", "dedup_sequences"):
        return None if isinstance(output, list) and output else "expected a non-empty list"
    if stage == "fold_msa":
        return None if isinstance(output, str) and output else "expected an msa handle"
    target = output.get(NESTED[stage], {}) if stage in NESTED else output
    missing = [k for k in EXPECTED.get(stage, ()) if k not in target]
    return f"missing keys {missing}" if missing else None


def run(stage: str, real: bool) -> bool:
    """Run one step and print what happened.

    Args:
        stage: Key in `STAGES`.
        real: Call CageFlow instead of the stand-in.

    Returns:
        True if it ran and the output looked right.
    """
    spec = STAGES[stage]
    backend = importlib.import_module(
        f"...stages.{'actual' if real else 'simulated'}", package=__package__)
    fn = getattr(backend, stage)
    kwargs = FIXTURES[stage]

    print(f"\nstep {spec.number}  {stage}  [{spec.hardware.value}]  "
          f"{'real' if real else 'simulated'}")
    print(f"  source   {spec.source or 'no CageFlow implementation'}")
    origin = "CNIO's real history.jsonl" if stage in _contract_from_real_run() else "our own stand-in"
    print(f"  contract {list(EXPECTED.get(stage, ()))}  (from {origin})")
    print(f"  input    {kwargs}")

    try:
        output = asyncio.run(fn(**kwargs)) if asyncio.iscoroutinefunction(fn) else fn(**kwargs)
    except NotImplementedError as exc:
        print(f"  BLOCKED  {exc}")
        return False

    reason = verify(stage, output)
    print(f"  output   {str(output)[:200]}")
    print(f"  {'PASS' if reason is None else 'FAIL  ' + reason}")
    print(f"  expected: {spec.check}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{spec.number}_{stage}.{'real' if real else 'simulated'}.json"
    path.write_text(json.dumps({
        "step": spec.number, "stage": stage, "backend": "real" if real else "simulated",
        "hardware": spec.hardware.value, "source": spec.source,
        "input": kwargs, "output": output,
        "passed": reason is None, "failure": reason,
        "expected": spec.check, "expected_cost_s": spec.cost_s,
        "host": socket.gethostname(),
    }, indent=2, default=str))
    print(f"  saved    {path.relative_to(Path.cwd()) if str(path).startswith(str(Path.cwd())) else path}")
    return reason is None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one CageFlow step on its own.")
    parser.add_argument("stage", nargs="?", choices=sorted(FIXTURES))
    parser.add_argument("--real", action="store_true", help="call CageFlow, not the stand-in")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    if not args.stage and not args.all:
        print("steps you can run:")
        for name in FIXTURES:
            print(f"  {STAGES[name].number:>3}  {name}")
        return

    names = list(FIXTURES) if args.all else [args.stage]
    passed = sum(run(n, args.real) for n in names)
    print(f"\n{passed}/{len(names)} passed")
    raise SystemExit(0 if passed == len(names) else 1)


if __name__ == "__main__":
    main()
