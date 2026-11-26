"""The tool runtime on its own: what it keeps of ended tasks."""

import asyncio
import gc
import traceback
import weakref

import pytest
from _basics import RUNS, broken, simulate

from flowgentic.tools import TaskInfo, ToolRuntime, task_tool


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
    assert runtime.stats() == {"submitted": n, "repeats": 0, "failed": n // 10, "refused": 0, "retained": n}
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
