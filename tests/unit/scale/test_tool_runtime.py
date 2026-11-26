"""The tool runtime on its own: what it keeps of ended tasks, where each task
runs, and a task run again after its worker was lost."""

import asyncio
import logging
import weakref

import pytest
from _basics import FAST, RUNS, ScriptedTransport, broken, inputs, reply, simulate, tool_results

from flowgentic.host import AgentHost
from flowgentic.loop import LoopRunner
from flowgentic.store import MemoryStore
from flowgentic.tools import TaskCancelled, TaskInfo, TaskRecord, ToolRuntime, task_tool


@pytest.fixture(autouse=True)
def clear_runs():
    RUNS.clear()


class InstantSession:
    """A RHAPSODY session whose tasks run as they are submitted, in the
    caller's thread: what the runtime keeps of many tasks, without a backend's
    own cost."""

    backends: dict = {}

    async def submit_tasks(self, tasks):
        loop = asyncio.get_running_loop()
        futures = []
        for task in tasks:
            future = loop.create_future()
            try:
                value = task["function"](*task["args"])
            except Exception as exc:
                future.set_exception(exc)
            else:
                future.set_result({"state": "DONE", "return_value": value})
            futures.append(future)
        return futures


async def test_the_runtime_keeps_outcomes_not_tasks():
    """An asyncio task kept after it ends costs a kilobyte or more, and Python
    walks every Task alive in the process each time ``asyncio.run`` ends, as a
    thread pool's async tasks do. Of 200,000 ended tasks, a tenth failed, the
    runtime keeps every outcome and no task."""
    n = 200_000
    runtime = ToolRuntime(InstantSession(), retained=n)
    sim, fails = task_tool(simulate), task_tool(broken)
    tasks = []
    for start in range(0, n, 10_000):
        jobs = [
            runtime.submit(fails if i % 10 == 0 else sim, f"a{i}", "c1", {"x": float(i)})
            for i in range(start, start + 10_000)
        ]
        await asyncio.wait(jobs)
        tasks += [weakref.ref(job) for job in jobs]
        del jobs
    assert [ref for ref in tasks if ref() is not None] == []  # freed as each ended
    assert runtime.stats() == {
        "submitted": n,
        "repeats": 0,
        "failed": n // 10,
        "refused": 0,
        "lost": 0,
        "retries": 0,
        "retained": n,
    }
    assert runtime._jobs == {}

    # A call made again gets the first call's outcome, and nothing runs again.
    ok = runtime.submit(sim, "a1", "c1", {"x": 1.0})
    failed = runtime.submit(fails, "a0", "c1", {"x": 0.0})
    assert ok.done() and ok.result() == {"y": 2.0}
    assert failed.done() and str(failed.exception()) == "no convergence at 0.0"
    assert failed.exception() is runtime.submit(fails, "a0", "c1", {"x": 0.0}).exception()
    assert RUNS == {"simulate": n - n // 10, "broken": n // 10}
    assert runtime.stats()["repeats"] == 3


class Refuse:
    async def admit(self, info: TaskInfo) -> None:
        raise RuntimeError("over budget")

    def done(self, info: TaskInfo) -> None:
        raise AssertionError("never admitted")


async def test_a_call_made_again_gets_each_kind_of_outcome_kept():
    """A refused task, and one cancelled once it was admitted, are kept as they
    ended; one withdrawn before its order admitted it is not."""
    refusing = ToolRuntime(InstantSession(), order=Refuse())
    first = refusing.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
    await asyncio.wait([first])
    again = refusing.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
    assert again.exception() is first.exception() and refusing.stats()["refused"] == 1

    started = asyncio.Event()

    async def slow(**kwargs):
        started.set()
        await asyncio.sleep(10)

    class Slow(InstantSession):
        async def submit_tasks(self, tasks):
            return [asyncio.ensure_future(slow())]

    runtime = ToolRuntime(Slow())
    job = runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
    await started.wait()
    job.cancel()
    await asyncio.wait([job])
    assert runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0}).cancelled()
    assert runtime.stats()["retained"] == 1


# --- where each task runs ----------------------------------------------------------


# --8<-- [start:least-loaded]
class LeastLoaded:
    """Place each task where the fewest of the runtime's tasks run: counted
    when it is placed, and again when its record arrives. ``places`` maps each
    place, such as a GPU, to the fields a task there gets."""

    def __init__(self, places: dict) -> None:
        self.places = places
        self.running = dict.fromkeys(places, 0)
        self.on: dict[str, object] = {}  # each running task's place, by its key

    def __call__(self, info: TaskInfo) -> dict:
        where = min(self.running, key=self.running.__getitem__)
        self.running[where] += 1
        self.on[info.key] = where
        return self.places[where]

    def ended(self, record: TaskRecord) -> None:
        if record.info.key in self.on:  # not a refused task: those are never placed
            self.running[self.on.pop(record.info.key)] -= 1


# --8<-- [end:least-loaded]


def on_fake_gpu(gpu: int) -> dict:
    return {"task_backend_specific_kwargs": {"gpu": gpu}}


class GpuSession(InstantSession):
    """A session whose tasks take as long as their GPU makes them, and which
    notes the fields each task reached it with."""

    def __init__(self, seconds: dict) -> None:
        self.seconds = seconds
        self.seen: list[dict] = []

    async def submit_tasks(self, tasks):
        out = []
        for task in tasks:
            self.seen.append(dict(task.get("task_backend_specific_kwargs") or {}))
            out.append(asyncio.ensure_future(self._run(task)))
        return out

    async def _run(self, task):
        gpu = (task.get("task_backend_specific_kwargs") or {}).get("gpu")
        await asyncio.sleep(self.seconds.get(gpu, 0.0))
        return {"state": "DONE", "return_value": task["function"](*task["args"])}


def simulate_times(n: int):
    """A model that calls simulate ``n`` times, one call per turn, then answers."""

    def script(messages):
        done = len(tool_results(messages))
        if done < n:
            return reply(calls=[(f"call-{done}", "simulate", {"x": float(done)})])
        return reply("done")

    return script


async def test_loop_agents_spread_over_two_gpus_by_load():
    """Tool-calling agents share one tool list, so a placement of the runtime
    chooses each call's GPU. GPU 0 is ten times slower: placed by load, most
    calls go to GPU 1, where round robin would split them evenly."""
    session = GpuSession({0: 0.1, 1: 0.01})
    placement = LeastLoaded({gpu: on_fake_gpu(gpu) for gpu in (0, 1)})
    runtime = ToolRuntime(session, place=placement)
    runtime.add_listener(placement.ended)
    runner = LoopRunner(ScriptedTransport(simulate_times(4)), [task_tool(simulate)], runtime=runtime)
    report = await AgentHost(runner, stagger=FAST).run(inputs(20))

    assert report.finished == 20 and RUNS == {"simulate": 80}
    assert [sorted(seen) for seen in session.seen] == [["gpu"]] * 80  # one GPU each
    on = [seen["gpu"] for seen in session.seen]
    assert on.count(0) + on.count(1) == 80 and on.count(1) > 2 * on.count(0) > 0
    assert placement.running == {0: 0, 1: 0} and placement.on == {}  # every record came


async def test_a_placement_goes_over_the_tools_fields_once_per_task():
    session = GpuSession({})
    placed = []

    def place(info: TaskInfo):
        placed.append(info.key)
        return None if info.args["x"] < 0 else {"task_backend_specific_kwargs": {"gpu": 1, "extra": True}}

    runtime = ToolRuntime(session, place=place, order=RefuseNegativeTwo())
    tool = task_tool(simulate, task_backend_specific_kwargs={"gpu": 9, "cpus": 2})
    assert await runtime.submit(tool, "a", "c1", {"x": 1.0}) == {"y": 2.0}
    assert await runtime.submit(tool, "a", "c1", {"x": 1.0}) == {"y": 2.0}  # a repeat: not placed
    assert await runtime.submit(tool, "a", "c2", {"x": -1.0}) == {"y": -2.0}  # None: the tool's own
    refused = runtime.submit(tool, "a", "c3", {"x": -2.0})  # refused: never placed
    await asyncio.wait([refused])
    assert session.seen == [{"gpu": 1, "cpus": 2, "extra": True}, {"gpu": 9, "cpus": 2}]
    assert placed == ["a/c1", "a/c2"]


class RefuseNegativeTwo:
    """An order that refuses the calls with x = -2, and notes each task that
    gives its place back."""

    def __init__(self) -> None:
        self.ended: list[str] = []

    async def admit(self, info: TaskInfo) -> None:
        if info.args["x"] == -2.0:
            raise RuntimeError("over budget")

    def done(self, info: TaskInfo) -> None:
        self.ended.append(info.key)


@pytest.mark.parametrize(
    "place, error",
    [
        (lambda info: {"backend": "gpus"}, "as placed: the RHAPSODY session has no backend 'gpus'"),
        (lambda info: ["gpu", 1], "as placed: a placement gives a task's fields: a dict, not list"),
        (lambda info: 1 / 0, "division by zero"),
    ],
    ids=["unknown-backend", "not-a-dict", "raises"],
)
async def test_a_placement_that_fails_fails_its_task(place, error):
    """The agent reads the error, as it reads a failed task's: the order's
    place is given back, and nothing runs."""
    order = RefuseNegativeTwo()
    runtime = ToolRuntime(GpuSession({}), place=place, order=order)
    records = []
    runtime.add_listener(records.append)
    job = runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
    await asyncio.wait([job])
    assert error in str(job.exception()) and "simulate" not in RUNS
    assert [(r.outcome, r.t_admit is not None) for r in records] == [("error", True)]
    assert runtime.stats()["failed"] == 1 and order.ended == ["a/c1"]


# --- a task run again after its worker was lost ------------------------------------


class WorkerLost(RuntimeError):
    """A backend's error for a task whose worker died."""


def lost_worker(error: Exception) -> bool:
    """The application's test for a lost worker, by its backend's error."""
    return isinstance(error, WorkerLost)


class LosingSession(InstantSession):
    """A session whose runs of the calls in ``lose``, by their ``x``, lose
    their worker, each as many times as ``lose`` says, then run; only those of
    ``agent``, if given. It notes each run's uid."""

    def __init__(self, lose: dict, error=WorkerLost, agent: str | None = None) -> None:
        self.lose = dict(lose)
        self.error = error
        self.agent = agent
        self.uids: list[str] = []

    async def submit_tasks(self, tasks):
        loop = asyncio.get_running_loop()
        out = []
        for task in tasks:
            self.uids.append(task["uid"])
            call = task["args"][2]["x"]
            future = loop.create_future()
            mine = self.agent is None or task["uid"].startswith(f"flowgentic.{self.agent}.")
            if mine and self.lose.get(call, 0) > 0:
                self.lose[call] -= 1
                future.set_exception(self.error(f"worker 0 exited with code -9 while running {task['uid']}"))
            else:
                future.set_result({"state": "DONE", "return_value": task["function"](*task["args"])})
            out.append(future)
        return out


def three_calls_then_sum(messages):
    """A model that calls simulate with x = 1, 2 and 3, one call per turn, then
    answers with the sum of the results, or with what failed."""
    import json

    results = tool_results(messages)
    failed = [m["content"] for m in results if "failed" in m["content"]]
    if failed:
        return reply(failed[0])
    if len(results) < 3:
        n = len(results)
        return reply(calls=[(f"call-{n}", "simulate", {"x": float(n + 1)})])
    return reply(f"total {sum(json.loads(m['content'])['y'] for m in results)}")


async def run_agents(session, tool, n: int = 4):
    runtime = ToolRuntime(session)
    records: list[TaskRecord] = []
    runtime.add_listener(records.append)
    outputs: dict = {}
    host = AgentHost(
        LoopRunner(ScriptedTransport(three_calls_then_sum), [tool], runtime=runtime, store=MemoryStore()),
        stagger=FAST,
        on_done=lambda r, out: outputs.__setitem__(r.id, out.messages[-1]["content"]),
    )
    report = await host.run(inputs(n))
    return report, outputs, records, runtime


@pytest.mark.parametrize("suspend", [False, True], ids=["waits", "suspends"])
async def test_a_task_that_loses_its_worker_once_runs_again_under_the_same_future(suspend, metric_reader):
    """Agent 1's second call loses its worker once. Its agent sees one future
    and the clean run's result; the call has two records, lost then ok."""
    tool = task_tool(simulate, suspend=suspend, retries=2, retry_on=lost_worker)
    clean = await run_agents(LosingSession({}), tool)
    RUNS.clear()
    session = LosingSession({2.0: 1}, agent="agent-1")
    report, outputs, records, runtime = await run_agents(session, tool)

    assert report.finished == 4 and outputs == clean[1] and outputs["agent-1"] == "total 12.0"
    assert all(r.suspensions == (3 if suspend else 0) for r in report.agents)
    runs = [r for r in records if r.info.agent_id == "agent-1" and r.info.args["x"] == 2.0]
    assert [(r.outcome, r.attempt) for r in runs] == [("lost", 1), ("ok", 2)]
    assert runs[0].info is runs[1].info and runs[0].value is None and runs[1].value == {"y": 4.0}
    assert "WorkerLost: worker 0 exited with code -9" in runs[0].error
    key = runs[0].info.key
    assert [u for u in session.uids if u.startswith(ToolRuntime.uid(key))] == [
        ToolRuntime.uid(key),
        ToolRuntime.uid(key, 2),
    ]
    assert runs[0].uid == ToolRuntime.uid(key) and runs[1].uid == ToolRuntime.uid(key) + "_r2"
    assert runs[0].t_admit <= runs[0].t_end <= runs[1].t_admit <= runs[1].t_end
    assert len(records) == 13 and RUNS == {"simulate": 12}
    stats = runtime.stats()
    assert (stats["failed"], stats["lost"], stats["retries"], stats["repeats"]) == (0, 1, 1, 12 if suspend else 0)
    assert any(p.value >= 1 for p in counted(metric_reader, "flowgentic.tool.retries"))


def counted(reader, name):
    data = reader.get_metrics_data()
    return [
        p
        for resource in (data.resource_metrics if data else ())
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for p in metric.data.data_points
        if p.attributes.get("gen_ai.tool.name") == "simulate"
    ]


async def test_a_task_that_keeps_losing_its_worker_fails_after_its_retries():
    """Three runs at most: the last one's error reaches the model, and each run
    has its record, lost."""
    tool = task_tool(simulate, retries=2, retry_on=lost_worker)
    report, outputs, records, runtime = await run_agents(LosingSession({2.0: 5}), tool, n=1)

    assert outputs["agent-0"].startswith("simulate failed: WorkerLost: worker 0 exited")
    runs = [r for r in records if r.info.args["x"] == 2.0]
    assert [(r.outcome, r.attempt) for r in runs] == [("lost", 1), ("lost", 2), ("lost", 3)]
    stats = runtime.stats()
    assert (stats["failed"], stats["lost"], stats["retries"]) == (1, 3, 2)


@pytest.mark.parametrize(
    "error, retry_on",
    [
        (ValueError, lost_worker),  # the task's own error
        (WorkerLost, None),  # no retry asked for
        (TaskCancelled, lambda error: True),  # cancelled: never run again
    ],
    ids=["own-error", "no-retry-on", "cancelled"],
)
async def test_only_a_lost_worker_runs_again(error, retry_on):
    tool = task_tool(simulate, retries=2 if retry_on else 0, retry_on=retry_on)
    runtime = ToolRuntime(LosingSession({1.0: 1}, error=error))
    records = []
    runtime.add_listener(records.append)
    job = runtime.submit(tool, "a", "c1", {"x": 1.0})
    await asyncio.wait([job])
    assert isinstance(job.exception(), error) and len(records) == 1
    assert records[0].outcome == ("cancelled" if error is TaskCancelled else "error")
    assert runtime.stats()["retries"] == 0


async def test_a_retry_on_that_raises_runs_nothing_again(caplog):
    tool = task_tool(simulate, retries=1, retry_on=lambda error: 1 / 0)
    runtime = ToolRuntime(LosingSession({1.0: 1}))
    with caplog.at_level(logging.WARNING, logger="flowgentic.tools"):
        job = runtime.submit(tool, "a", "c1", {"x": 1.0})
        await asyncio.wait([job])
    assert isinstance(job.exception(), WorkerLost) and runtime.stats()["retries"] == 0
    assert "retry_on of task tool simulate failed on WorkerLost" in caplog.text


def test_retries_need_a_retry_on_and_are_not_negative():
    with pytest.raises(ValueError, match="retries needs retry_on"):
        task_tool(simulate, retries=1)
    with pytest.raises(ValueError, match="retries is -1"):
        task_tool(simulate, retries=-1, retry_on=lost_worker)
    assert task_tool(simulate, retries=3, retry_on=lost_worker).retries == 3


async def test_each_run_is_placed_and_ends_in_a_record():
    """A run again is placed again, so a placement by load counts it."""
    placement = LeastLoaded({gpu: on_fake_gpu(gpu) for gpu in (0, 1)})
    placed = []

    def place(info):
        fields = placement(info)
        placed.append(fields["task_backend_specific_kwargs"]["gpu"])
        return fields

    runtime = ToolRuntime(LosingSession({1.0: 1}), place=place)
    runtime.add_listener(placement.ended)
    tool = task_tool(simulate, retries=1, retry_on=lost_worker)
    assert await runtime.submit(tool, "a", "c1", {"x": 1.0}) == {"y": 2.0}
    assert placed == [0, 0] and placement.running == {0: 0, 1: 0}
