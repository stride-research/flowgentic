"""The tool runtime on its own: what it keeps of ended tasks, where each task
runs, and a task run again after its worker was lost."""

import asyncio
import gc
import logging
import traceback
import weakref

import pytest
from _basics import FAST, RUNS, ScriptedTransport, broken, inputs, reply, simulate, tool_results

from flowgentic.host import AgentHost
from flowgentic.loop import LoopRunner
from flowgentic.store import MemoryStore
from flowgentic.tools import Immediate, TaskCancelled, TaskInfo, TaskRecord, ToolRuntime, ToolSetupError, task_tool


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


async def test_a_failed_outcome_raised_again_keeps_the_traceback_it_ended_with():
    """Each repeat raises the same exception, from the traceback its task ended
    with: the callers that raised it before neither grow the traceback nor
    stay alive in it."""
    runtime = ToolRuntime(InstantSession())
    fails = task_tool(broken)
    first = runtime.submit(fails, "a", "c1", {"x": 0.0})
    await asyncio.wait([first])
    callers = []

    class Caller:
        pass

    async def call_again():
        me = Caller()
        callers.append(weakref.ref(me))
        try:
            await runtime.submit(fails, "a", "c1", {"x": 0.0})
        except ValueError as exc:
            assert exc is first.exception()
            return len(list(traceback.walk_tb(exc.__traceback__)))

    depths = [await call_again() for _ in range(400)]
    gc.collect()
    assert len(set(depths)) == 1
    # Only the last caller, until the next repeat.
    assert len([ref for ref in callers if ref() is not None]) <= 1
    assert RUNS == {"broken": 1}


class Big:
    """Stands for a large argument."""

    def __repr__(self) -> str:
        return "big"


async def test_a_failed_outcome_keeps_its_arguments_until_the_runtime_drops_it():
    """A failed task's exception keeps its traceback, whose frames keep the
    call's arguments, for as long as the runtime keeps its outcome."""
    runtime = ToolRuntime(InstantSession(), retained=1)
    fails = task_tool(broken)
    big = Big()
    kept = weakref.ref(big)
    job = runtime.submit(fails, "a", "c1", {"x": big})
    await asyncio.wait([job])
    del big, job
    gc.collect()
    assert kept() is not None
    await asyncio.wait([runtime.submit(fails, "a", "c2", {"x": 1.0})])
    gc.collect()
    assert kept() is None


def test_a_call_made_again_on_no_event_loop_gets_the_outcome_kept():
    """Called again for an ended task from code that runs on no event loop,
    ``submit`` gives a future on the loop of the runtime's last task."""
    runtime = ToolRuntime(InstantSession())
    sim = task_tool(simulate)

    async def first():
        return await runtime.submit(sim, "a", "c1", {"x": 1.0})

    assert asyncio.run(first()) == {"y": 2.0}
    again = runtime.submit(sim, "a", "c1", {"x": 1.0})
    assert again.done() and again.result() == {"y": 2.0}
    assert RUNS == {"simulate": 1} and runtime.stats()["repeats"] == 1


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
        (lambda info: 1 / 0, "place failed for a/c1: ZeroDivisionError: division by zero"),
    ],
    ids=["unknown-backend", "not-a-dict", "raises"],
)
async def test_a_placement_set_up_wrong_fails_its_task_with_tool_setup_error(place, error):
    """A runtime set up wrong, not a call's error: the order's place is given
    back, and nothing runs."""
    order = RefuseNegativeTwo()
    runtime = ToolRuntime(GpuSession({}), place=place, order=order)
    records = []
    runtime.add_listener(records.append)
    job = runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
    await asyncio.wait([job])
    assert isinstance(job.exception(), ToolSetupError)
    assert error in str(job.exception()) and "simulate" not in RUNS
    assert [(r.outcome, r.t_admit is not None) for r in records] == [("error", True)]
    assert runtime.stats()["failed"] == 1 and order.ended == ["a/c1"]


async def test_a_placement_set_up_wrong_fails_the_agent_before_its_model_reads_it():
    """A mistyped GPU fails each agent that calls the tool, as a tool's unknown
    backend does: no model spends its turns on the error."""
    turns = []

    def script(messages):
        turns.append(len(messages))
        if tool_results(messages):
            return reply("done")
        return reply(calls=[("call-0", "simulate", {"x": 1.0})])

    runtime = ToolRuntime(GpuSession({}), place=lambda info: {"backend": "gpus"})
    runner = LoopRunner(ScriptedTransport(script), [task_tool(simulate)], runtime=runtime)
    report = await AgentHost(runner, stagger=FAST).run(inputs(2))
    assert [agent.status for agent in report.agents] == ["failed", "failed"]
    for agent in report.agents:
        assert agent.error.startswith("ToolSetupError: task tool simulate, as placed: ")
    assert len(turns) == 2 and "simulate" not in RUNS  # one model call per agent


class HangingSession(InstantSession):
    """A session whose tasks end only as a test ends them."""

    def __init__(self) -> None:
        self.futures: list[asyncio.Future] = []

    async def submit_tasks(self, tasks):
        self.futures += [asyncio.get_running_loop().create_future() for _ in tasks]
        return self.futures[-len(tasks) :]

    async def close(self) -> None:
        return None


@pytest.mark.parametrize(
    "end, outcome",
    [("lost", "error"), ("rhapsody", "cancelled"), ("session", "cancelled"), ("agent", "cancelled")],
    ids=["worker-lost", "cancelled-by-rhapsody", "session-closed", "cancelled-by-agent"],
)
async def test_a_placed_task_gives_its_place_back_however_it_ends(end, outcome):
    """Its one record arrives however the task ends, so a placement by load
    counts it once more."""
    session = HangingSession()
    placement = LeastLoaded({gpu: on_fake_gpu(gpu) for gpu in (0, 1)})
    runtime = ToolRuntime(session, place=placement)
    records = []
    runtime.add_listener(placement.ended)
    runtime.add_listener(records.append)
    job = runtime.submit(task_tool(simulate), "a", "c1", {"x": 1.0})
    while not session.futures:
        await asyncio.sleep(0)
    assert placement.running == {0: 1, 1: 0}
    (future,) = session.futures
    if end == "lost":
        future.set_exception(RuntimeError("worker 3 exited with code -9"))
    elif end == "rhapsody":
        future.set_result({"state": "CANCELED"})
    elif end == "session":
        await session.close()
    else:
        job.cancel()
    await asyncio.wait([job])
    assert [record.outcome for record in records] == [outcome]
    assert placement.running == {0: 0, 1: 0} and placement.on == {}


class NamedBackends(GpuSession):
    """A session with two backends, which notes each task's backend too."""

    backends = {"cpu": InstantSession(), "gpu": InstantSession()}

    async def submit_tasks(self, tasks):
        self.seen += [
            {"backend": task.get("backend"), **(task.get("task_backend_specific_kwargs") or {})} for task in tasks
        ]
        return [asyncio.ensure_future(self._run(task)) for task in tasks]


@pytest.mark.parametrize(
    "placed, seen",
    [
        ({"task_backend_specific_kwargs": None}, {"backend": "gpu"}),  # all the tool's own fields dropped
        ({"task_backend_specific_kwargs": {"cpus": 1}}, {"backend": "gpu", "gpu": 9, "cpus": 1}),
        ({"backend": None}, {"backend": "cpu", "gpu": 9}),  # the runtime's backend, not the tool's
    ],
    ids=["no-backend-fields", "fields-key-by-key", "no-backend"],
)
async def test_a_placement_merges_shallowly_over_the_tools_fields(placed, seen):
    session = NamedBackends({})
    runtime = ToolRuntime(session, backend="cpu", place=lambda info: placed)
    tool = task_tool(simulate, backend="gpu", task_backend_specific_kwargs={"gpu": 9})
    assert await runtime.submit(tool, "a", "c1", {"x": 1.0}) == {"y": 2.0}
    assert session.seen == [seen]


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


async def test_retry_on_only_tells_a_lost_worker_and_the_runtime_counts_the_runs():
    """retry_on is asked once for each run that fails and says only whether its
    worker was lost; the runtime alone decides whether it runs again. So every
    lost run is recorded lost, the last one too, and counted as such."""
    asked = []

    def tells(error: Exception) -> bool:
        asked.append(error)
        return lost_worker(error)

    tool = task_tool(simulate, retries=1, retry_on=tells)
    runtime = ToolRuntime(LosingSession({1.0: 9}))
    records = []
    runtime.add_listener(records.append)
    job = runtime.submit(tool, "a", "c1", {"x": 1.0})
    await asyncio.wait([job])
    assert len(asked) == 2 and isinstance(job.exception(), WorkerLost)
    assert [(r.outcome, r.attempt) for r in records] == [("lost", 1), ("lost", 2)]
    stats = runtime.stats()
    assert (stats["failed"], stats["lost"], stats["retries"]) == (1, 2, 1)


# --8<-- [start:run-again-at-most]
class RunAgainAtMost(Immediate):
    """Let the tasks this order admits run again after a lost worker ``n``
    times in all: a budget shared by many tasks, such as a population's."""

    def __init__(self, n: int) -> None:
        self.left = n

    def again(self, info: TaskInfo) -> bool:
        if self.left == 0:
            return False
        self.left -= 1
        return True


# --8<-- [end:run-again-at-most]


async def test_an_order_can_refuse_a_run_again_and_the_run_stays_lost():
    """The order is asked before each run again that the tool's retries allow,
    with that run's info; a run it refuses ends the task, recorded lost."""
    order = RunAgainAtMost(2)
    asked = []
    allows = order.again

    def again(info: TaskInfo) -> bool:
        asked.append((info.call_id, info.attempt, info.lost_error.split(":")[0]))
        return allows(info)

    order.again = again
    tool = task_tool(simulate, retries=5, retry_on=lost_worker)
    runtime = ToolRuntime(LosingSession({1.0: 9, 2.0: 9, 3.0: 9}), order=order)
    records = []
    runtime.add_listener(records.append)
    jobs = []
    for call, x in (("c1", 1.0), ("c2", 2.0)):
        jobs.append(runtime.submit(tool, "a", call, {"x": x}))
        await asyncio.wait(jobs[-1:])
    spent = runtime.submit(task_tool(simulate, retries=1, retry_on=lost_worker), "a", "c3", {"x": 3.0})
    await asyncio.wait([spent])
    assert asked == [("c1", 2, "WorkerLost"), ("c1", 3, "WorkerLost"), ("c1", 4, "WorkerLost"), ("c2", 2, "WorkerLost"), ("c3", 2, "WorkerLost")]
    assert [(r.info.call_id, r.outcome, r.attempt) for r in records] == [
        ("c1", "lost", 1),
        ("c1", "lost", 2),
        ("c1", "lost", 3),  # refused
        ("c2", "lost", 1),  # refused
        ("c3", "lost", 1),  # refused: the budget is spent
    ]
    assert all(isinstance(job.exception(), WorkerLost) for job in (*jobs, spent))
    stats = runtime.stats()
    assert (stats["lost"], stats["retries"], stats["failed"]) == (5, 2, 3)


async def test_again_is_not_asked_once_the_tools_retries_are_spent_or_for_its_own_error():
    order = RunAgainAtMost(10)
    tool = task_tool(simulate, retries=1, retry_on=lost_worker)
    runtime = ToolRuntime(LosingSession({1.0: 9}), order=order)
    lost = runtime.submit(tool, "a", "c1", {"x": 1.0})
    await asyncio.wait([lost])
    own = runtime.submit(task_tool(broken, retries=1, retry_on=lost_worker), "a", "c2", {"x": 2.0})
    await asyncio.wait([own])
    assert order.left == 9  # asked once: before c1's second run
    assert isinstance(lost.exception(), WorkerLost) and isinstance(own.exception(), ValueError)


async def async_again(info):
    return True


class Raising(Immediate):
    def again(self, info: TaskInfo) -> bool:
        raise RuntimeError("no budget here")


class Awaiting(Immediate):
    def again(self, info: TaskInfo):
        return async_again(info)


@pytest.mark.parametrize(
    "order, warning",
    [(Raising(), "the task order's again failed on a/c1"), (Awaiting(), "returned an awaitable, not a bool")],
    ids=["raises", "async"],
)
async def test_an_again_that_fails_refuses_the_run_again(order, warning, caplog):
    tool = task_tool(simulate, retries=1, retry_on=lost_worker)
    runtime = ToolRuntime(LosingSession({1.0: 1}), order=order)
    with caplog.at_level(logging.WARNING, logger="flowgentic.tools"):
        job = runtime.submit(tool, "a", "c1", {"x": 1.0})
        await asyncio.wait([job])
    assert isinstance(job.exception(), WorkerLost) and runtime.stats()["retries"] == 0
    assert warning in caplog.text


async def test_an_order_without_again_lets_every_run_again_go_ahead():
    """Orders written before ``again`` existed are unchanged."""
    order = RefuseNegativeTwo()  # admit and done only
    tool = task_tool(simulate, retries=2, retry_on=lost_worker)
    runtime = ToolRuntime(LosingSession({1.0: 2}), order=order)
    assert await runtime.submit(tool, "a", "c1", {"x": 1.0}) == {"y": 2.0}
    assert runtime.stats()["retries"] == 2 and order.ended == ["a/c1"]


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


async def test_each_run_is_placed_and_a_run_again_can_avoid_where_its_worker_was_lost():
    """A run again is placed again, with its attempt and where the run before
    lost its worker, so a placement can move it to another GPU; a placement
    by load counts each run."""
    placement = LeastLoaded({gpu: on_fake_gpu(gpu) for gpu in (0, 1)})
    seen = []

    def place(info: TaskInfo):
        seen.append((info.attempt, info.lost_on, info.lost_error))
        if info.lost_on is not None:  # not where the worker was lost
            lost_on = info.lost_on["task_backend_specific_kwargs"]["gpu"]
            placement.running[lost_on] += 1_000
            try:
                return placement(info)
            finally:
                placement.running[lost_on] -= 1_000
        return placement(info)

    session = LosingSession({1.0: 1})
    runtime = ToolRuntime(session, place=place)
    runtime.add_listener(placement.ended)
    tool = task_tool(simulate, retries=1, retry_on=lost_worker)
    assert await runtime.submit(tool, "a", "c1", {"x": 1.0}) == {"y": 2.0}
    assert [attempt for attempt, _, _ in seen] == [1, 2]
    assert seen[0][1:] == (None, None)
    assert seen[1][1] == {"task_backend_specific_kwargs": {"gpu": 0}}
    assert seen[1][2].startswith("WorkerLost: worker 0 exited with code -9")
    assert placement.running == {0: 0, 1: 0} and placement.on == {}
    assert runtime.stats()["retries"] == 1


async def test_a_run_again_that_cannot_be_placed_is_not_counted():
    """Placed wrong, the run again never starts: the task fails with
    ``ToolSetupError``, and the runtime counts no run again."""

    def place(info: TaskInfo):
        if info.attempt > 1:
            raise RuntimeError("no GPU left")

    runtime = ToolRuntime(LosingSession({1.0: 1}), place=place)
    records = []
    runtime.add_listener(records.append)
    job = runtime.submit(task_tool(simulate, retries=1, retry_on=lost_worker), "a", "c1", {"x": 1.0})
    await asyncio.wait([job])
    assert isinstance(job.exception(), ToolSetupError) and "no GPU left" in str(job.exception())
    assert [(r.outcome, r.attempt) for r in records] == [("lost", 1), ("error", 2)]
    stats = runtime.stats()
    assert (stats["lost"], stats["retries"], stats["failed"]) == (1, 0, 1)


async def is_lost(error: Exception) -> bool:
    return True


class AsyncTest:
    async def __call__(self, error: Exception) -> bool:
        return True


@pytest.mark.parametrize("retry_on", [is_lost, AsyncTest()], ids=["async-def", "async-call"])
def test_retry_on_must_be_synchronous(retry_on):
    with pytest.raises(ValueError, match="retry_on must be synchronous"):
        task_tool(simulate, retries=1, retry_on=retry_on)


async def test_a_retry_on_that_returns_an_awaitable_says_no(caplog):
    tool = task_tool(simulate, retries=1, retry_on=lambda error: is_lost(error))
    runtime = ToolRuntime(LosingSession({1.0: 1}))
    with caplog.at_level(logging.WARNING, logger="flowgentic.tools"):
        job = runtime.submit(tool, "a", "c1", {"x": 1.0})
        await asyncio.wait([job])
    assert isinstance(job.exception(), WorkerLost) and runtime.stats()["retries"] == 0
    assert "returned an awaitable, not a bool" in caplog.text


@pytest.mark.parametrize("when", ["during-the-first-run", "between-runs"])
async def test_a_lost_task_whose_agent_has_ended_does_not_run_again(when):
    """Once its agent has ended and no call waits for it, as when its host was
    stopped, a task whose worker is lost does not run again: no one would read
    its outcome. Its last run is recorded lost, and the task fails."""
    session = HangingSession()
    tool = task_tool(simulate, retries=3, retry_on=lost_worker)
    runtime = ToolRuntime(session)
    records = []
    runtime.add_listener(records.append)
    runner = LoopRunner(ScriptedTransport(simulate_times(1)), [tool], runtime=runtime)
    host = asyncio.ensure_future(AgentHost(runner, stagger=FAST).run(inputs(1)))
    while not session.futures:
        await asyncio.sleep(0)
    if when == "between-runs":  # the first run is lost while the agent waits: it runs again
        session.futures[0].set_exception(WorkerLost("worker 3 exited with code -9"))
        while len(session.futures) < 2:
            await asyncio.sleep(0)
    (job,) = runtime._jobs.values()
    host.cancel()  # the host stops, and with it the agent
    await asyncio.wait([host])
    assert not job.done()  # started: RHAPSODY does not stop it
    session.futures[-1].set_exception(WorkerLost("worker 4 exited with code -9"))
    await asyncio.wait([job], timeout=5)  # a run again here would never end
    runs = 1 if when == "during-the-first-run" else 2
    assert job.done(), f"{len(session.futures)} runs"
    assert len(session.futures) == runs and isinstance(job.exception(), WorkerLost)
    assert [(r.outcome, r.attempt) for r in records] == [("lost", n) for n in range(1, runs + 1)]
    assert runtime.stats()["retries"] == runs - 1


async def test_a_task_cancelled_between_its_runs_ends_cancelled():
    """Cancelled while its run again is being submitted, the task ends
    cancelled: the lost run's record, then the cancelled one's."""
    started = asyncio.Event()

    class SlowToSubmit(LosingSession):
        async def submit_tasks(self, tasks):
            if len(self.uids) == 1:  # the run again
                started.set()
                await asyncio.sleep(10)
            return await super().submit_tasks(tasks)

    runtime = ToolRuntime(SlowToSubmit({1.0: 1}))
    records = []
    runtime.add_listener(records.append)
    job = runtime.submit(task_tool(simulate, retries=1, retry_on=lost_worker), "a", "c1", {"x": 1.0})
    await started.wait()
    job.cancel()
    await asyncio.wait([job])
    assert job.cancelled() and "simulate" not in RUNS
    assert [(r.outcome, r.attempt) for r in records] == [("lost", 1), ("cancelled", 2)]
    assert runtime.stats()["retries"] == 1


async def test_a_run_again_is_an_event_on_the_calls_span(span_exporter):
    tool = task_tool(simulate, retries=2, retry_on=lost_worker)
    runtime = ToolRuntime(LosingSession({1.0: 1}))
    assert await runtime.submit(tool, "a", "c1", {"x": 1.0}) == {"y": 2.0}
    (span,) = [s for s in span_exporter.get_finished_spans() if s.name == "execute_tool simulate"]
    assert span.attributes["flowgentic.tool.attempts"] == 2
    assert span.attributes["flowgentic.tool.outcome"] == "ok"
    (event,) = span.events
    assert event.name == "flowgentic.tool.retry"
    assert dict(event.attributes) == {"flowgentic.tool.attempt": 1, "error.type": "WorkerLost"}
