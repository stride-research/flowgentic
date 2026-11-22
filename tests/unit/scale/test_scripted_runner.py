"""A runner of the application's own, in an agent host: code, not a model,
picks each step. ``flowgentic.host``'s ``current_agent``, ``activity`` and
``wait`` are what it needs to account its agents' states and wait on the tasks
of a ``ToolRuntime``, as Flowgentic's own runners do. No LangChain: they run
without it installed."""

import asyncio
import time

import pytest
from _basics import FAST, RUNS, count, simulate, thread_session

from flowgentic.host import (
    Agent,
    AgentHost,
    AgentState,
    Finished,
    Suspended,
    activity,
    current_agent,
    wait,
)
from flowgentic.tools import TaskInfo, ToolRuntime, task_tool


@pytest.fixture(autouse=True)
def clear_runs():
    RUNS.clear()


def slow_simulate(x: float) -> dict:
    """A simulation that takes a while."""
    time.sleep(0.3)
    count("slow_simulate")
    return {"y": 2 * x}


class OneAtATime:
    """An order policy that admits one task at a time."""

    def __init__(self):
        self.slot = asyncio.Semaphore(1)

    async def admit(self, info: TaskInfo) -> None:
        await self.slot.acquire()

    def done(self, info: TaskInfo) -> None:
        self.slot.release()


class Scripted:
    """Each agent looks its input up, runs a simulation as a task, waits for it
    in the loop, and finishes with what it found. A runner suspends on a task
    the way this one waits on it, through ``register_pending``; the public
    functions are the same."""

    def __init__(self, runtime: ToolRuntime, tool) -> None:
        self.runtime = runtime
        self.tool = tool
        self.agents: dict[str, str | None] = {}

    async def lookup(self, what: object) -> float:
        with activity(AgentState.TOOL_CALL):
            await asyncio.sleep(0.02)
        return 1.5

    async def start(self, agent: Agent):
        self.agents[agent.id] = current_agent()
        x = await self.lookup(agent.input)
        job = self.runtime.submit(self.tool, agent.id, "sim0", {"x": x})
        with activity(AgentState.TOOL_WAIT):
            await wait((job,))
        return Finished(job.result())

    async def resume(self, agent: Agent, done: dict):
        raise AssertionError("never suspends")


async def test_a_scripted_runner_accounts_its_agents_states():
    session = await thread_session()
    runner = Scripted(ToolRuntime(session), task_tool(simulate))
    outputs = {}
    host = AgentHost(runner, stagger=FAST, on_done=lambda r, out: outputs.__setitem__(r.id, out))
    report = await host.run(Agent(f"agent-{i}", "x") for i in range(5))
    await session.close()

    assert report.finished == 5
    assert runner.agents == {f"agent-{i}": f"agent-{i}" for i in range(5)}
    assert outputs["agent-0"] == {"y": 3.0}
    seconds = host.states.agent_seconds()
    assert seconds[AgentState.TOOL_CALL] >= 5 * 0.02 * 0.9
    assert seconds[AgentState.TOOL_WAIT] > 0
    assert current_agent() is None  # outside the agents' tasks


async def test_wait_does_not_raise_the_tasks_errors():
    loop = asyncio.get_running_loop()
    ok, failed = loop.create_future(), loop.create_future()
    loop.call_later(0.01, failed.set_exception, ValueError("boom"))
    loop.call_later(0.02, ok.set_result, 1)
    await wait((ok, failed))
    assert ok.result() == 1 and isinstance(failed.exception(), ValueError)
    await wait(())  # nothing to wait for


async def test_a_task_its_order_has_not_admitted_is_withdrawn_when_its_wait_is_cancelled():
    """Stopping a scripted agent while it waits withdraws the tasks it asked for
    that never started; one already running runs on."""
    session = await thread_session()
    runtime = ToolRuntime(session, order=OneAtATime())
    tool = task_tool(slow_simulate)
    records = []
    runtime.add_listener(records.append)

    async def agent(i: int):
        job = runtime.submit(tool, f"agent-{i}", "sim0", {"x": float(i)})
        with activity(AgentState.TOOL_WAIT):
            await wait((job,))

    waits = [asyncio.create_task(agent(i)) for i in range(3)]
    await asyncio.sleep(0.1)  # one task runs; two wait for their turn
    for w in waits:
        w.cancel()
    await asyncio.gather(*waits, return_exceptions=True)
    await asyncio.sleep(0.8)  # time for two more tasks, had they been submitted
    await session.close()
    assert RUNS == {"slow_simulate": 1}
    assert sorted((r.outcome, r.t_admit is None) for r in records) == [
        ("cancelled", True),
        ("cancelled", True),
        ("ok", False),
    ]


@pytest.mark.parametrize("state", [AgentState.PENDING, AgentState.WORKING, AgentState.DONE])
async def test_activity_takes_only_the_states_a_runner_enters(state):
    """An agent is working when no activity is open, and the host alone makes
    it pending or done."""

    async def agent():
        with pytest.raises(ValueError, match=state.name):
            with activity(state):
                pass

    with pytest.raises(ValueError, match=state.name):  # outside an agent too
        with activity(state):
            pass
    host = AgentHost(_Once(agent), stagger=FAST)
    report = await host.run([Agent("a", None)])
    assert report.finished == 1


class _Once:
    def __init__(self, body):
        self.body = body

    async def start(self, agent: Agent):
        await self.body()
        return Finished(None)

    async def resume(self, agent: Agent, done: dict):
        return Suspended(())
