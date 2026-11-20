"""Measure a model client's CPU per call against a local OpenAI-compatible stub.

The stub, in a process of its own, answers every chat completion with the same
reply, a tool call of about 40 tokens, after ``--delay`` seconds, so that
``N`` calls are in flight at the client. By default the delay is what vLLM's
server took per call in G1 at the same ``N`` (Qwen3-8B on one GPU): 0.6 s at
1, 1.3 s at 64, 8.9 s at 512. A much shorter delay makes the single-threaded
client the bottleneck, and its cost per call is then not a client's cost. The client, in this process, keeps
``N`` calls going: after ``N + 20`` calls have finished, a window opens; it
closes after ``--calls`` calls. Over the window: the process's CPU time (all
threads) per call, calls per second, and the call's mean time. Each call sends
a conversation of about 1,100 tokens with three tools, as the agents-at-scale
demo's do.

Clients:

- ``transport``: Flowgentic's ``OpenAITransport`` (the openai SDK, and the
  reply read into a ``Reply``);
- ``asyncopenai``: the openai SDK's ``AsyncOpenAI`` alone, the floor under it;
- ``chatopenai``: LangChain's ``ChatOpenAI``, bound to the tools, on LangChain
  messages, as a LangGraph graph calls it;
- ``chatopenai-adapter``: ``ChatOpenAI`` through Flowgentic's LangChain adapter
  on OpenAI-format messages, as the agent loop calls a LangChain model;
- ``httpx``: one ``httpx.AsyncClient`` POST and ``json.loads``, a floor under
  the SDK;
- ``transport-8``: ``OpenAITransport`` over eight SDK clients taken in turn,
  each with its own connection pool;
- ``transport-auto``: ``OpenAITransport`` as it is made by default, opening
  another client, with its own pool, for every 64 calls in flight.

    python benchmarks/client_cost.py --client transport --in-flight 64
    python benchmarks/client_cost.py --repeat 3   # every client, N = 1, 64, 512

The method follows G1's "a real client's cost per call", with a stub in place
of the model server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import socket
import subprocess
import sys
import time
from typing import Any

REPLY = {
    "id": "chatcmpl-stub",
    "object": "chat.completion",
    "created": 1794826800,
    "model": "Qwen3-8B",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "reasoning": None,
                "tool_calls": [
                    {
                        "id": "chatcmpl-tool-stub",
                        "type": "function",
                        "function": {
                            "name": "simulate",
                            "arguments": json.dumps(
                                {"candidate": "cand-000042", "temperature": 480.0, "dopant": 0.02}
                            ),
                        },
                    }
                ],
            },
            "logprobs": None,
            "finish_reason": "tool_calls",
            "stop_reason": None,
        }
    ],
    "usage": {
        "prompt_tokens": 1130,
        "total_tokens": 1170,
        "completion_tokens": 40,
        "prompt_tokens_details": {"cached_tokens": 1024},
    },
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": list(properties)},
        },
    }
    for name, description, properties in (
        ("lookup", "Reference data for a candidate.", {"candidate": {"type": "string"}}),
        (
            "simulate",
            "Simulate a candidate at a temperature and dopant fraction; return its score.",
            {
                "candidate": {"type": "string"},
                "temperature": {"type": "number"},
                "dopant": {"type": "number"},
            },
        ),
        ("note", "Write a note in the agent's log.", {"text": {"type": "string"}}),
    )
]


def conversation() -> list[dict]:
    """About 4,500 characters, 1,100 tokens: a system prompt, the task, and
    three turns of tool calls and results."""
    messages = [
        {"role": "system", "content": "You tune one candidate's synthesis temperature and dopant fraction. " * 12},
        {"role": "user", "content": "Find the best temperature and dopant fraction for cand-000042."},
    ]
    for k in range(3):
        args = {"candidate": "cand-000042", "temperature": 400.0 + 40 * k, "dopant": 0.01 * k}
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": f"call-{k}", "type": "function", "function": {"name": "simulate", "arguments": json.dumps(args)}}
                ],
            }
        )
        result = {"score": 0.5 + k / 10, "notes": "converging steadily " * 20, "values": [k / 7] * 24}
        messages.append({"role": "tool", "tool_call_id": f"call-{k}", "content": json.dumps(result)})
    return messages


# --- the stub server ------------------------------------------------------------------


def serve(port: int, delay: float) -> None:
    """A minimal HTTP/1.1 server on asyncio, with keep-alive: it reads each
    request whole, waits ``delay``, and sends the same reply. Lighter than a
    framework, so that the stub never limits the client."""
    body = json.dumps(REPLY).encode()
    response = (
        b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
        b"content-length: " + str(len(body)).encode() + b"\r\nconnection: keep-alive\r\n\r\n" + body
    )

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                length = 0
                for line in head.split(b"\r\n"):
                    if line[:15].lower() == b"content-length:":
                        length = int(line[15:])
                if length:
                    await reader.readexactly(length)
                await asyncio.sleep(delay)
                writer.write(response)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    async def main() -> None:
        server = await asyncio.start_server(handle, "127.0.0.1", port, backlog=4096, limit=1 << 20)
        async with server:
            await server.serve_forever()

    asyncio.run(main())


class Stub:
    def __init__(self, delay: float) -> None:
        self.delay = delay

    def __enter__(self) -> Stub:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        self.proc = subprocess.Popen(
            [sys.executable, __file__, "--serve", str(self.port), "--delay", str(self.delay)]
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                return self
            except OSError:
                if self.proc.poll() is not None or time.monotonic() > deadline:
                    self.proc.kill()
                    raise RuntimeError("the stub did not start") from None
                time.sleep(0.05)

    def __exit__(self, *exc: object) -> None:
        self.proc.terminate()
        self.proc.wait(10)


# --- the clients ------------------------------------------------------------------------


def make_client(kind: str, url: str, in_flight: int) -> Any:
    """An async function that makes one call and returns the number of tool
    calls in the reply."""
    messages = conversation()
    limits = max(in_flight + 16, 100)
    if kind == "httpx":
        import httpx

        client = httpx.AsyncClient(
            base_url=url,
            timeout=600,
            limits=httpx.Limits(max_connections=limits, max_keepalive_connections=limits),
        )
        body = {"model": "Qwen3-8B", "messages": messages, "tools": TOOLS}

        async def call() -> int:
            r = await client.post("/chat/completions", json=body, headers={"Authorization": "Bearer EMPTY"})
            return len(json.loads(r.content)["choices"][0]["message"]["tool_calls"])

        return call
    import httpx
    from openai import AsyncOpenAI

    sdk = AsyncOpenAI(
        base_url=url,
        api_key="EMPTY",
        max_retries=0,
        timeout=600,
        http_client=httpx.AsyncClient(
            limits=httpx.Limits(max_connections=limits, max_keepalive_connections=limits)
        ),
    )
    if kind == "asyncopenai":

        async def call() -> int:
            out = await sdk.chat.completions.create(model="Qwen3-8B", messages=messages, tools=TOOLS)
            return len(out.choices[0].message.tool_calls)

        return call
    if kind == "transport":
        from flowgentic.fleet import OpenAITransport

        transport = OpenAITransport("Qwen3-8B", client=sdk)

        async def call() -> int:
            reply = await transport.complete(messages, TOOLS)
            return len(reply.tool_calls)

        return call
    if kind == "transport-auto":
        from flowgentic.fleet import OpenAITransport

        auto = OpenAITransport("Qwen3-8B", base_url=url, max_retries=0, timeout=600)

        async def call() -> int:
            reply = await auto.complete(messages, TOOLS)
            return len(reply.tool_calls)

        return call
    if kind == "transport-8":
        # the same transport over 8 clients, each with its own connection
        # pool, taken in turn: whether the pool's cost grows with its calls
        from itertools import cycle

        from flowgentic.fleet import OpenAITransport

        per = max(in_flight // 8 + 16, 32)
        transports = cycle(
            OpenAITransport(
                "Qwen3-8B",
                client=AsyncOpenAI(
                    base_url=url,
                    api_key="EMPTY",
                    max_retries=0,
                    timeout=600,
                    http_client=httpx.AsyncClient(
                        limits=httpx.Limits(max_connections=per, max_keepalive_connections=per)
                    ),
                ),
            )
            for _ in range(8)
        )

        async def call() -> int:
            reply = await next(transports).complete(messages, TOOLS)
            return len(reply.tool_calls)

        return call
    from langchain_openai import ChatOpenAI

    chat = ChatOpenAI(model="Qwen3-8B", base_url=url, api_key="EMPTY", max_retries=0, async_client=sdk.chat.completions)
    bound = chat.bind_tools(TOOLS)
    if kind == "chatopenai":
        from flowgentic.langchain import from_openai_messages

        lc_messages = from_openai_messages(messages)  # a graph holds LangChain's

        async def call() -> int:
            reply = await bound.ainvoke(lc_messages)
            return len(reply.tool_calls)

        return call
    if kind == "chatopenai-adapter":
        from flowgentic.langchain import ChatModelTransport

        transport = ChatModelTransport(chat)

        async def call() -> int:
            reply = await transport.complete(messages, TOOLS)
            return len(reply.tool_calls)

        return call
    raise ValueError(f"no client {kind!r}")


async def measure(kind: str, url: str, in_flight: int, calls: int) -> dict:
    call = make_client(kind, url, in_flight)
    warmup = in_flight + 20
    done = 0
    window: dict = {}
    durations: list[float] = []
    stop = asyncio.Event()

    async def worker() -> None:
        nonlocal done
        while not stop.is_set():
            t = time.perf_counter()
            n = await call()
            assert n == 1  # noqa: S101  # the stub's reply holds one tool call
            done += 1
            if done == warmup:
                window["cpu"] = time.process_time()
                window["wall"] = time.perf_counter()
            elif done > warmup:
                durations.append(time.perf_counter() - t)
                if done == warmup + calls:
                    window["cpu_end"] = time.process_time()
                    window["wall_end"] = time.perf_counter()
                    stop.set()

    workers = [asyncio.create_task(worker()) for _ in range(in_flight)]
    stopped = asyncio.create_task(stop.wait())
    await asyncio.wait([stopped, *workers], return_when=asyncio.FIRST_COMPLETED)
    for w in workers:
        w.cancel()
    results = await asyncio.gather(*workers, return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    if errors:  # a call failed: say how, rather than wait for ever
        raise errors[0]
    cpu = window["cpu_end"] - window["cpu"]
    wall = window["wall_end"] - window["wall"]
    return {
        "client": kind,
        "in_flight": in_flight,
        "calls": calls,
        "cpu_per_call_us": round(1e6 * cpu / calls, 1),
        "calls_per_s": round(calls / wall, 1),
        "cores": round(cpu / wall, 3),
        "call_s_mean": round(sum(durations) / len(durations), 4),
        "python": platform.python_version(),
        "machine": platform.machine(),
    }


CLIENTS = ["transport", "asyncopenai", "chatopenai", "chatopenai-adapter", "httpx", "transport-8", "transport-auto"]


# vLLM's mean time per call in G1, by calls in flight (Qwen3-8B, one GPU).
G1_SERVER_S = {1: 0.6, 64: 1.3, 512: 8.9}


def delay_for(n: int, given: float) -> float:
    if given:
        return given
    return G1_SERVER_S.get(n) or min(G1_SERVER_S.items(), key=lambda kv: abs(kv[0] - n))[1]


# vLLM's mean time per call in G1, by calls in flight (Qwen3-8B, one GPU).
G1_SERVER_S = {1: 0.6, 64: 1.3, 512: 8.9}


def delay_for(n: int, given: float) -> float:
    if given:
        return given
    return G1_SERVER_S.get(n) or min(G1_SERVER_S.items(), key=lambda kv: abs(kv[0] - n))[1]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--client", choices=CLIENTS, default="transport")
    p.add_argument("--in-flight", type=int, default=64)
    p.add_argument("--calls", type=int, default=0, help="calls in the window (default: 400 at N=1, else 2,000)")
    p.add_argument(
        "--delay", type=float, default=0.0, help="the stub's time per call, in seconds (default: G1's)"
    )
    p.add_argument("--repeat", type=int, default=0)
    p.add_argument("--clients", type=lambda s: s.split(","), default=CLIENTS)
    p.add_argument("--sizes", type=lambda s: [int(x) for x in s.split(",")], default=[1, 64, 512])
    p.add_argument("--url", help=argparse.SUPPRESS)
    p.add_argument("--serve", type=int, help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.serve:
        serve(a.serve, a.delay)
        return
    if a.repeat:
        for rep in range(a.repeat):
            for n in a.sizes:
                with Stub(delay_for(n, a.delay)) as stub:
                    for kind in a.clients:
                        out = subprocess.run(
                            [
                                sys.executable, __file__, "--client", kind, "--in-flight", str(n),
                                "--url", stub.url, "--calls", str(a.calls),
                            ],  # fmt: skip
                            capture_output=True, text=True, check=False, timeout=900,
                        )
                        if out.returncode != 0:
                            sys.exit(out.stderr)
                        line = json.loads(out.stdout.strip().splitlines()[-1])
                        line["rep"] = rep
                        line["delay_s"] = stub.delay
                        print(json.dumps(line), flush=True)
        return
    calls = a.calls or (100 if a.in_flight == 1 else 2000)
    if a.url:
        print(json.dumps(asyncio.run(measure(a.client, a.url, a.in_flight, calls))))
        return
    with Stub(delay_for(a.in_flight, a.delay)) as stub:
        out = asyncio.run(measure(a.client, stub.url, a.in_flight, calls))
        out["delay_s"] = stub.delay
        print(json.dumps(out))


if __name__ == "__main__":
    main()
