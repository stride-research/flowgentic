"""The run-metrics schema, fixed across every run kind (v1 sync, v2 async, v3 intelligent).

One schema, deliberately. A field that a given run cannot populate is None,
never absent: `None` is an answer ("this executor has no pool, so pool
utilization is undefined"), whereas a missing key is ambiguous between
"doesn't apply", "not collected yet", and "collector bug". Keeping the shape
constant is also what makes two runs diffable without special-casing.

See METRICS.md in this folder for where every field comes from, why it
matters, and how to read it against the others.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class StageCompute(BaseModel):
    """One (stage, resource) row of Laura's own Budget.compute_report().

    Always populated, for every run kind: her Executor.map_timed() wraps each
    task in _timed_call regardless of which backend is underneath, so this
    survives even the plain `sequential` run with nothing of ours in the loop.
    """

    stage: str
    resource: str
    n_tasks: int
    elapsed_s_total: float
    elapsed_s_mean: float
    elapsed_s_max: float
    cpu_s_total: float
    #: >1 means tasks used more than one core internally (torch/BLAS/PyRosetta
    #: threading); ~1 single-threaded; well under 1 means they spent the time
    #: waiting, not computing. Her wording, her metric.
    cpu_per_elapsed: float
    #: Per-worker-PROCESS high-water mark, not per task -- the number to size
    #: --mem against. Process-level, so unaffected by node sharing.
    peak_rss_mb_max: float
    hosts: list[str]


class BudgetPool(BaseModel):
    """One resource pool's charged spend, from Laura's Budget.summary().

    `total` is float('inf') unless config["budget"] caps it, which no config of
    ours does yet -- so in practice this is tracking, not gating. Serialized as
    None when infinite, since JSON has no infinity.
    """

    resource: str
    total_hours: float | None
    spent_hours: float
    remaining_hours: float | None


class AllocationPool(BaseModel):
    """What the CLUSTER was charged for one pool, vs what tasks consumed.

    Only a backend that genuinely reserves cluster resources can answer this:
    Executor.allocation_report() returns {} on the base class, so the
    `sequential` reference run has nothing here. The gap between this and
    BudgetPool.spent_hours is the thesis's headline resource number (Laura
    measured ~30:1 on her own cluster: a cpu pool held ~3h for 367s of task
    time, blocked waiting on a gpu worker).
    """

    resource: str
    jobs: int
    wall_s: float
    core_hours: float
    gpu_hours: float


class PoolUtilization(BaseModel):
    """How busy a worker pool actually was, per resource.

    Not emitted by RHAPSODY -- its ResourceUpdate events are per-node and
    pool-unaware (verified in rhapsody/telemetry/adapters/concurrent.py). This
    is derived by us: mean_concurrency from the task lifecycle timestamps in
    the telemetry JSONL, pool_size from what we configured. Expect
    utilization ~= 1/pool_size on a straight-line v1 DAG -- a real baseline,
    not a bug, and the number that should climb once v2 adds concurrency.
    """

    resource: str
    pool_size: int
    mean_concurrency: float
    max_concurrency: int
    utilization: float


class TaskOverhead(BaseModel):
    """Framework cost around the science, from RHAPSODY lifecycle event deltas.

    Undefined for the reference run and left None there -- SequentialExecutor
    runs fn(*args) inline inside submit(), so there is no queue, no distinct
    submitted/queued/started transition, nothing to measure. Not "a small
    overhead we didn't record": the states do not exist.

    overhead_s = e2e_s - exec_s, i.e. everything that was not the science.
    """

    resource: str
    n_tasks: int
    routing_s_mean: float
    queue_wait_s_mean: float
    queue_wait_s_max: float
    exec_s_mean: float
    e2e_s_mean: float
    overhead_s_mean: float


class NodeResources(BaseModel):
    """Node-level resource time series, summarized, from RHAPSODY ResourceUpdate.

    CAVEAT, and it is not small: these are psutil/pynvml readings for the whole
    NODE, not for our job (confirmed in the adapter source -- psutil.cpu_percent,
    psutil.virtual_memory, pynvml device utilization). On scitech, partitions
    carry OverSubscribe=FORCE:2, so another user's work can share the node and
    inflate every number here. Trust StageCompute.cpu_per_elapsed and
    peak_rss_mb_max for job-isolated figures; treat these as context.

    gpu_percent is None when no NVIDIA device is visible. per_gpu rows carry a
    gpu_id; the node aggregate carries gpu_id=None.
    """

    node_id: str
    gpu_id: int | None
    n_samples: int
    cpu_percent_mean: float | None
    cpu_percent_max: float | None
    memory_percent_mean: float | None
    memory_percent_max: float | None
    gpu_percent_mean: float | None
    gpu_percent_max: float | None


class DockScience(BaseModel):
    """Per-dock scientific outcome, assembled from her history.jsonl.

    `status` is her own taxonomy, and the terminal ones are informative:
    geometry_failed / not_selected / design_failed docks legitimately carry no
    downstream numbers rather than zeros.
    """

    dock_index: int
    status: str
    dock_score: float | None = None
    radius: float | None = None
    spin: float | None = None
    n_clashes: int | None = None
    n_contacts: int | None = None
    selected: bool = False
    n_sequences: int | None = None
    mpnn_scores: list[float] = Field(default_factory=list)
    group_score: float | None = None
    #: Dense, one entry per designed sequence: 0.0 is her explicit sentinel for
    #: "never actually scored" (missed rosetta_score_cutoff), NOT a measured
    #: neutral binding energy. Do not average this blindly -- see group_ddg.
    per_seq_ddg: list[float] = Field(default_factory=list)
    #: Computed over only the sequences that actually cleared the cutoff;
    #: None if none did.
    group_ddg: float | None = None


class FoldScience(BaseModel):
    """One folded (dock, sequence) prediction.

    Record-only in the pipeline: nothing downstream gates on these. rmsd can be
    None with a populated `error` while the fold itself succeeded -- a
    prediction that landed but could not be compared (chain-count mismatch)
    keeps its pLDDT/ipTM rather than being discarded.
    """

    dock_index: int
    seq_index: int
    ok: bool
    mean_plddt: float | None = None
    ptm: float | None = None
    iptm: float | None = None
    rmsd: float | None = None
    error: str | None = None


class RosettaMetrics(BaseModel):
    """The full Rosetta interface metric set for one scored sequence.

    Three families, and conflating them is a real error:
      * unprefixed -- copy 0 against EVERY other copy, which merges the point
        group's symmetry-equivalent interfaces into one measurement. Fine as a
        relative ranking, wrong as an absolute.
      * pair_* -- the same fields recomputed on ONE ISOLATED oligomer pair.
        Her measured example on one packed pose: sc 0.254 (merged) vs 0.435
        (pair), dSASA 3782 vs 2252.
      * dG_int_symmetric -- Rosetta's own symmetric interface energy, counted
        off the symdef rather than a chosen chain split.
    Both unprefixed and pair_ are kept so earlier runs stay comparable and an
    optimizer can use either; any report must say which it quotes.

    Stored as a free-form dict rather than ~40 explicit fields: the authoritative
    list is metric_keys.ALL_METRIC_KEYS in HER repo, and restating it here would
    be a second copy to drift out of sync.
    """

    dock_index: int
    seq_index: int
    metrics: dict[str, float]


class ScienceMetrics(BaseModel):
    """Everything the pipeline measured, as opposed to what it cost."""

    docks: list[DockScience] = Field(default_factory=list)
    rosetta: list[RosettaMetrics] = Field(default_factory=list)
    folds: list[FoldScience] = Field(default_factory=list)
    n_qualifying_sequences: int = 0


class RunMetrics(BaseModel):
    """One run, science and system side by side.

    Deliberately has no "run_kind" label: `executor` and `orchestrator` are
    facts read off the run itself and are what actually determine which optional
    sections can be populated, whereas a hand-assigned kind is a name we would
    have to keep honest ourselves.
    """

    #: config["executor"] verbatim -- "sequential", "asyncflow", ...
    executor: str
    #: None when her CLI ran unassisted; "asyncflow-<version>" when ours drove it.
    orchestrator: str | None = None
    out_dir: str
    #: The literal config dict the run was given, for provenance.
    config: dict = Field(default_factory=dict)
    wall_s: float | None = None

    science: ScienceMetrics = Field(default_factory=ScienceMetrics)

    # --- system: always available, from HER instrumentation ---
    compute: list[StageCompute] = Field(default_factory=list)
    budget: list[BudgetPool] = Field(default_factory=list)

    # --- system: require a backend that reserves or reports. None, not [],
    # when the concept does not apply to this run's executor. ---
    allocation: list[AllocationPool] | None = None
    pool_utilization: list[PoolUtilization] | None = None
    overhead: list[TaskOverhead] | None = None
    node_resources: list[NodeResources] | None = None
