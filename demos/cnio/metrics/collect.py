"""Read a finished CageFlow run's output directory into a RunMetrics JSON.

Reads HER files only -- history.jsonl, qualifying_sequences.csv,
compute_stats.json, budget_summary.json, allocation_stats.json -- and never
imports CageFlow. That is deliberate: this couples us to her documented OUTPUT
formats, a far smaller and more stable surface than her function signatures.
A run can therefore be collected on any machine, with no PyRosetta, no
ProteinMPNN and no fold stack installed.

Nothing here asserts or gates. It reformats. Correctness of the run itself is
established by the per-step isolation tests and by diffing two runs' science
against each other, not by this script.

Usage:
    python collect.py <cageflow_out_dir> [-o metrics.json] [--executor NAME]
                      [--orchestrator NAME] [--wall-s SECONDS]
                      [--telemetry run.jsonl]
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

from schema import (
    AllocationPool,
    BudgetPool,
    DockScience,
    FoldScience,
    NodeResources,
    PoolUtilization,
    RosettaMetrics,
    RunMetrics,
    ScienceMetrics,
    StageCompute,
    TaskOverhead,
)


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _finite_or_none(value: float | None) -> float | None:
    """inf/nan -> None, so the result is valid JSON.

    Budget totals default to float('inf') when uncapped (build_budget), and
    json.dump would emit bare `Infinity`, which is not valid JSON and breaks
    strict parsers.
    """
    if value is None:
        return None
    return value if math.isfinite(value) else None


def collect_compute(out_dir: Path) -> list[StageCompute]:
    """compute_stats.json -> StageCompute rows, her field names unchanged."""
    rows = []
    for rec in json.loads((out_dir / "compute_stats.json").read_text()) if (
        out_dir / "compute_stats.json"
    ).exists() else []:
        rows.append(StageCompute(**rec))
    return rows


def collect_budget(out_dir: Path) -> list[BudgetPool]:
    """budget_summary.json ({resource: {total, spent, remaining}}) -> rows."""
    path = out_dir / "budget_summary.json"
    if not path.exists():
        return []
    return [
        BudgetPool(
            resource=resource,
            total_hours=_finite_or_none(pool.get("total")),
            spent_hours=pool.get("spent", 0.0),
            remaining_hours=_finite_or_none(pool.get("remaining")),
        )
        for resource, pool in json.loads(path.read_text()).items()
    ]


def collect_allocation(out_dir: Path) -> list[AllocationPool] | None:
    """allocation_stats.json -> rows, or None when the executor cannot answer.

    Absent file and empty dict both mean the same thing and both map to None:
    Executor.allocation_report() returns {} unless the backend genuinely holds
    a cluster allocation. Not an error, and not zero either -- undefined.
    """
    path = out_dir / "allocation_stats.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if not data:
        return None
    return [
        AllocationPool(
            resource=resource,
            jobs=alloc.get("jobs", 0),
            wall_s=alloc.get("wall_s", 0.0),
            core_hours=alloc.get("core_hours", 0.0),
            gpu_hours=alloc.get("gpu_hours", 0.0),
        )
        for resource, alloc in data.items()
    ]


def collect_science(out_dir: Path) -> ScienceMetrics:
    """history.jsonl + qualifying_sequences.csv -> ScienceMetrics.

    history.jsonl holds one record per (dock, stage-outcome), appended as the
    run progresses, so a dock that got all the way through has several lines.
    We fold them per dock, last-write-wins per field, which matches how her own
    history_table._last_by_status reads the file -- and means completion-ordered
    appends are fine, not just dock-ordered ones.
    """
    history = _load_jsonl(out_dir / "history.jsonl")

    # The `design` sub-dict carries DIFFERENT fields per status -- "designed"
    # has n_sequences/best_score, "scored" adds per_seq_scores/group_score,
    # "rosetta_scored" adds per_seq_metrics/group_ddg, "folded" carries only
    # fold/n_folded/best_rmsd. A plain top-level update() therefore lets the
    # later record's `design` clobber the earlier one's, silently dropping
    # per_seq_metrics -- caught by running this against her committed run,
    # which reported 0 Rosetta-scored sequences where there were 2. So the
    # nested dicts are merged key-by-key rather than replaced.
    by_dock: dict[int, dict] = defaultdict(dict)
    for rec in history:
        idx = rec.get("dock_index")
        if idx is None:
            continue
        merged = by_dock[idx]
        for key, value in rec.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value

    docks: list[DockScience] = []
    rosetta: list[RosettaMetrics] = []
    folds: list[FoldScience] = []

    for idx in sorted(by_dock):
        rec = by_dock[idx]
        status = rec.get("status", "unknown")
        solution = rec.get("solution") or {}
        geometry = rec.get("geometry") or {}
        design = rec.get("design") or {}

        docks.append(
            DockScience(
                dock_index=idx,
                status=status,
                dock_score=solution.get("score"),
                radius=solution.get("radius"),
                spin=solution.get("spin"),
                n_clashes=geometry.get("n_clashes"),
                n_contacts=geometry.get("n_contacts"),
                selected=status not in ("geometry_failed", "not_selected"),
                n_sequences=design.get("n_sequences"),
                mpnn_scores=design.get("per_seq_scores") or [],
                group_score=design.get("group_score"),
                per_seq_ddg=design.get("per_seq_ddg") or [],
                group_ddg=design.get("group_ddg"),
            )
        )

        # Full per-sequence Rosetta metric dicts, when the dock reached scoring.
        for seq_index, metrics in enumerate(design.get("per_seq_metrics") or []):
            if isinstance(metrics, dict) and metrics:
                rosetta.append(
                    RosettaMetrics(dock_index=idx, seq_index=seq_index, metrics=metrics)
                )

        # fold_oligomer's results are nested INSIDE the design dict (verified
        # against her committed run: top-level keys are only dock_index/status/
        # solution/geometry/design/error), keyed by a STRINGIFIED sequence index
        # because it went through JSON.
        fold = design.get("fold") or {}
        for seq_index, result in (fold.items() if isinstance(fold, dict) else []):
            if not isinstance(result, dict):
                continue
            folds.append(
                FoldScience(
                    dock_index=idx,
                    seq_index=int(seq_index),
                    ok=bool(result.get("ok")),
                    mean_plddt=result.get("mean_plddt"),
                    ptm=result.get("ptm"),
                    iptm=result.get("iptm"),
                    rmsd=result.get("rmsd"),
                    error=result.get("error"),
                )
            )

    # Row count of her own scientist-facing table, read without pandas so this
    # script stays dependency-light (the header line is not a data row).
    csv_path = out_dir / "qualifying_sequences.csv"
    n_qualifying = 0
    if csv_path.exists():
        lines = [ln for ln in csv_path.read_text().splitlines() if ln.strip()]
        n_qualifying = max(0, len(lines) - 1)

    return ScienceMetrics(
        docks=docks,
        rosetta=rosetta,
        folds=folds,
        n_qualifying_sequences=n_qualifying,
    )


def collect_telemetry(
    path: Path, pool_sizes: dict[str, int] | None = None
) -> tuple[list[PoolUtilization] | None, list[TaskOverhead] | None, list[NodeResources] | None]:
    """RHAPSODY telemetry JSONL -> (pool utilization, overhead, node resources).

    Returns (None, None, None) when there is no telemetry file, which is the
    reference run's case: her `sequential` executor emits no lifecycle events
    because it has no lifecycle -- submit() runs the function inline.

    Overhead comes from deltas between the lifecycle events
    (TaskSubmitted -> TaskQueued -> TaskStarted -> TaskCompleted); concurrency
    is swept off the Started/Completed timestamps; node resources are summarized
    per (node_id, gpu_id) from ResourceUpdate events.
    """
    if not path or not path.exists():
        return None, None, None

    events = [
        rec
        for rec in _load_jsonl(path)
        if rec.get("section", "event") == "event"
    ]

    # --- overhead, per task then aggregated by resource ---
    stamps: dict[str, dict[str, float]] = defaultdict(dict)
    task_resource: dict[str, str] = {}
    for ev in events:
        task_id = ev.get("task_id")
        etype = ev.get("event_type")
        if not task_id or etype not in (
            "TaskSubmitted",
            "TaskQueued",
            "TaskStarted",
            "TaskCompleted",
            "TaskFailed",
        ):
            continue
        stamps[task_id][etype] = ev.get("event_time", 0.0)
        attrs = ev.get("attributes") or {}
        # Our own submissions tag the pool; fall back to "unknown" rather than
        # silently folding gpu work into the cpu row.
        task_resource.setdefault(task_id, attrs.get("resource") or "unknown")

    per_resource: dict[str, list[dict[str, float]]] = defaultdict(list)
    for task_id, st in stamps.items():
        submitted = st.get("TaskSubmitted")
        queued = st.get("TaskQueued")
        started = st.get("TaskStarted")
        done = st.get("TaskCompleted", st.get("TaskFailed"))
        if submitted is None or started is None or done is None:
            continue  # incomplete lifecycle -- excluded rather than zero-filled
        per_resource[task_resource.get(task_id, "unknown")].append(
            {
                "routing_s": (queued - submitted) if queued is not None else 0.0,
                "queue_wait_s": (started - queued) if queued is not None else (started - submitted),
                "exec_s": done - started,
                "e2e_s": done - submitted,
            }
        )

    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    overhead = [
        TaskOverhead(
            resource=resource,
            n_tasks=len(rows),
            routing_s_mean=_mean([r["routing_s"] for r in rows]),
            queue_wait_s_mean=_mean([r["queue_wait_s"] for r in rows]),
            queue_wait_s_max=max((r["queue_wait_s"] for r in rows), default=0.0),
            exec_s_mean=_mean([r["exec_s"] for r in rows]),
            e2e_s_mean=_mean([r["e2e_s"] for r in rows]),
            overhead_s_mean=_mean([r["e2e_s"] - r["exec_s"] for r in rows]),
        )
        for resource, rows in sorted(per_resource.items())
    ] or None

    # --- concurrency, swept over start/end timestamps per resource ---
    pool_utilization = []
    for resource, _rows in sorted(per_resource.items()):
        spans = [
            (stamps[t].get("TaskStarted"), stamps[t].get("TaskCompleted", stamps[t].get("TaskFailed")))
            for t in stamps
            if task_resource.get(t) == resource
        ]
        spans = [(a, b) for a, b in spans if a is not None and b is not None]
        if not spans:
            continue
        # Sweep line: +1 at each start, -1 at each end. Time-weighted mean over
        # the window from first start to last end, so idle gaps count against
        # utilization rather than being skipped.
        deltas: list[tuple[float, int]] = []
        for a, b in spans:
            deltas.append((a, 1))
            deltas.append((b, -1))
        deltas.sort()
        running = 0
        peak = 0
        area = 0.0
        prev_t = deltas[0][0]
        for t, d in deltas:
            area += running * (t - prev_t)
            prev_t = t
            running += d
            peak = max(peak, running)
        window = deltas[-1][0] - deltas[0][0]
        mean_concurrency = (area / window) if window > 0 else float(peak)
        size = (pool_sizes or {}).get(resource, peak)
        pool_utilization.append(
            PoolUtilization(
                resource=resource,
                pool_size=size,
                mean_concurrency=mean_concurrency,
                max_concurrency=peak,
                utilization=(mean_concurrency / size) if size else 0.0,
            )
        )

    # --- node/gpu resource series, summarized ---
    samples: dict[tuple[str, int | None], list[dict]] = defaultdict(list)
    for ev in events:
        if ev.get("event_type") != "ResourceUpdate":
            continue
        samples[(ev.get("node_id") or "unknown", ev.get("gpu_id"))].append(ev)

    def _agg(rows: list[dict], key: str, how) -> float | None:
        values = [r[key] for r in rows if r.get(key) is not None]
        return how(values) if values else None

    node_resources = [
        NodeResources(
            node_id=node_id,
            gpu_id=gpu_id,
            n_samples=len(rows),
            cpu_percent_mean=_agg(rows, "cpu_percent", lambda v: sum(v) / len(v)),
            cpu_percent_max=_agg(rows, "cpu_percent", max),
            memory_percent_mean=_agg(rows, "memory_percent", lambda v: sum(v) / len(v)),
            memory_percent_max=_agg(rows, "memory_percent", max),
            gpu_percent_mean=_agg(rows, "gpu_percent", lambda v: sum(v) / len(v)),
            gpu_percent_max=_agg(rows, "gpu_percent", max),
        )
        for (node_id, gpu_id), rows in sorted(samples.items(), key=lambda kv: (kv[0][0], kv[0][1] is not None, kv[0][1] or 0))
    ] or None

    return (pool_utilization or None), overhead, node_resources


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir", type=Path, help="a finished run's CageFlow out_dir")
    ap.add_argument("-o", "--output", type=Path, default=None, help="default: <out_dir>/metrics.json")
    ap.add_argument("--executor", default=None, help="override; default read from the run's config")
    ap.add_argument("--orchestrator", default=None, help="e.g. asyncflow-0.5.1; omit for her plain CLI")
    ap.add_argument("--wall-s", type=float, default=None, help="measured wall-clock of the whole run")
    ap.add_argument("--telemetry", type=Path, default=None, help="RHAPSODY telemetry JSONL, if any")
    args = ap.parse_args()

    out_dir: Path = args.out_dir
    if not out_dir.exists():
        raise SystemExit(f"not a directory: {out_dir}")

    # She does not copy the config into out_dir, so the executor name is only
    # knowable from the caller unless we are told. Default to the documented
    # CageFlow default rather than guessing something else.
    config: dict = {}
    config_copy = out_dir / "config.used.json"
    if config_copy.exists():
        config = json.loads(config_copy.read_text())

    pool_sizes = {}
    if config.get("max_workers"):
        pool_sizes["cpu"] = int(config["max_workers"])
    if config.get("max_workers_gpu"):
        pool_sizes["gpu"] = int(config["max_workers_gpu"])

    pool_utilization, overhead, node_resources = collect_telemetry(args.telemetry, pool_sizes)

    metrics = RunMetrics(
        executor=args.executor or config.get("executor", "sequential"),
        orchestrator=args.orchestrator,
        out_dir=str(out_dir),
        config=config,
        wall_s=args.wall_s,
        science=collect_science(out_dir),
        compute=collect_compute(out_dir),
        budget=collect_budget(out_dir),
        allocation=collect_allocation(out_dir),
        pool_utilization=pool_utilization,
        overhead=overhead,
        node_resources=node_resources,
    )

    dest = args.output or (out_dir / "metrics.json")
    dest.write_text(metrics.model_dump_json(indent=2))

    sci = metrics.science
    print(f"wrote {dest}")
    print(f"  executor         {metrics.executor}"
          f"{' / ' + metrics.orchestrator if metrics.orchestrator else ''}")
    print(f"  docks            {len(sci.docks)}")
    print(f"  rosetta-scored   {len(sci.rosetta)} sequence(s)")
    print(f"  folded           {len(sci.folds)}")
    print(f"  qualifying rows  {sci.n_qualifying_sequences}")
    print(f"  compute stages   {len(metrics.compute)}")
    for section in ("allocation", "pool_utilization", "overhead", "node_resources"):
        value = getattr(metrics, section)
        print(f"  {section:16} {'n/a (this executor)' if value is None else f'{len(value)} row(s)'}")


if __name__ == "__main__":
    main()
