"""Shared pieces for the tests of the agent host, its runners and the tools
that need no LangChain: RHAPSODY tasks, and transports on OpenAI-format
messages. ``_support`` adds LangChain's."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from flowgentic.host import Agent, Stagger

RUNS: dict[str, int] = {}
_RUNS_LOCK = threading.Lock()


def count(name: str) -> None:
    with _RUNS_LOCK:
        RUNS[name] = RUNS.get(name, 0) + 1


def simulate(x: float) -> dict:
    """Run a simulation of x."""
    count("simulate")
    return {"y": 2 * x}


def broken(x: float) -> dict:
    """A simulation that fails."""
    count("broken")
    raise ValueError(f"no convergence at {x}")


async def thread_session(workers: int = 8):
    """A RHAPSODY session on a thread pool, as a laptop runs one."""
    from rhapsody import Session
    from rhapsody.backends import ConcurrentExecutionBackend

    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor(max_workers=workers))
    return Session(backends=[backend])


# --- transports: OpenAI-format messages, no LangChain ------------------------------


def reply(content="", calls=(), usage=(10, 2), reasoning=None, cached=None, thinking=None):
    """A Reply: ``calls`` are (id, name, arguments) triples, the arguments a
    dict, or a string that becomes an invalid call."""
    from flowgentic.fleet import InvalidToolCall, Reply, ToolCall, Usage

    valid = tuple(ToolCall(i, n, a) for i, n, a in calls if isinstance(a, dict))
    invalid = tuple(
        InvalidToolCall(i, n, a, "not valid JSON") for i, n, a in calls if isinstance(a, str)
    )
    return Reply(
        content=content,
        tool_calls=valid,
        invalid_tool_calls=invalid,
        usage=None if usage is None else Usage(usage[0], usage[1], cached, thinking),
        reasoning=reasoning,
        finish_reason="tool_calls" if calls else "stop",
        id="r-1",
        model="scripted",
    )


class ScriptedTransport:
    """A transport that answers ``script(messages)``: a Reply, or a string for
    a plain answer. It keeps each call's messages, tools and keywords."""

    def __init__(self, script, model="scripted", base_url=None, latency_s=0.0):
        self.script = script
        self.model = model
        self.base_url = base_url
        self.latency_s = latency_s
        self.calls = []

    async def complete(self, messages, tools=None, **kwargs):
        self.calls.append((list(messages), tools, kwargs))
        if self.latency_s:
            import asyncio

            await asyncio.sleep(self.latency_s)
        out = self.script(list(messages))
        return reply(out) if isinstance(out, str) else out


def tool_results(messages):
    return [m for m in messages if m["role"] == "tool"]


def dict_calls_then_answer(*calls):
    """First turn: the given tool calls, at once; then answer with the tools'
    results, joined. On OpenAI-format messages."""

    def script(messages):
        results = tool_results(messages)
        if not results:
            return reply(calls=[(f"call-{i}", name, args) for i, (name, args) in enumerate(calls)])
        return reply(" | ".join(str(m["content"]) for m in results))

    return script


# --- agents in a host -----------------------------------------------------------------

FAST = Stagger(batch=50, interval_s=0.0)


class LookupArgs(BaseModel):
    material: str


def inputs(n: int, prefix: str = "agent") -> list[Agent]:
    return [Agent(f"{prefix}-{i}", "go") for i in range(n)]


def collect(outputs: dict):
    """An ``on_done`` that keeps each agent's output by its id."""
    return lambda result, output: outputs.__setitem__(result.id, output)


def transport(*calls, **kwargs):
    """A transport that makes ``calls`` at once, then answers with their results."""
    return ScriptedTransport(dict_calls_then_answer(*calls), **kwargs)
