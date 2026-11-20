"""Record chat completions from an OpenAI-compatible server as contract
fixtures for ``test_transports.py``.

    python tests/unit/scale/completions/record.py http://127.0.0.1:8000 \\
        --server "vLLM 0.19.1" --prefix vllm-0.19.1-recorded \\
        --about "Qwen3-8B; hermes tool parser, qwen3 reasoning parser"

Each case is one request, sent with temperature 0; its file keeps the
request, the server's response as it came, and the ``Reply`` a transport
must read from it. The expectation is read from the response's fields
here, without Flowgentic's parser, so a recording checks the parser rather
than repeats it. ``--thinking`` sends Qwen3's ``enable_thinking`` switch,
which vLLM and SGLang read from ``chat_template_kwargs``.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Reference data for a candidate: a temperature to start from.",
            "parameters": {
                "type": "object",
                "properties": {"candidate": {"type": "string"}},
                "required": ["candidate"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate",
            "description": "Simulate a candidate at a temperature; return its score.",
            "parameters": {
                "type": "object",
                "properties": {
                    "candidate": {"type": "string"},
                    "temperature": {"type": "number"},
                },
                "required": ["candidate", "temperature"],
            },
        },
    },
]
SYSTEM = {"role": "system", "content": "You tune one candidate. Use the tools; answer with the best score."}
FIRST = [SYSTEM, {"role": "user", "content": "Find the best temperature for cand-000001. Look it up first."}]


def _call(call_id: str, name: str, args: dict) -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


AFTER = [
    *FIRST,
    {"role": "assistant", "content": "", "tool_calls": [_call("call-1", "lookup", {"candidate": "cand-000001"})]},
    {"role": "tool", "tool_call_id": "call-1", "content": json.dumps({"candidate": "cand-000001", "start_temperature": 480.0})},
]
BOTH = [SYSTEM, {"role": "user", "content": "Simulate cand-000001 at 400 and at 500, both at once, in one reply."}]
DONE = [
    *AFTER,
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [_call("call-2", "simulate", {"candidate": "cand-000001", "temperature": 480.0})],
    },
    {"role": "tool", "tool_call_id": "call-2", "content": json.dumps({"score": 0.98})},
]
OFF, ON = False, True
CASES = {
    "tool-call": ({"messages": FIRST, "tools": TOOLS}, OFF),
    "tool-call-again-cached": ({"messages": FIRST, "tools": TOOLS}, OFF),
    "tool-call-after-result": ({"messages": AFTER, "tools": TOOLS}, OFF),
    "two-tool-calls": ({"messages": BOTH, "tools": TOOLS}, OFF),
    "answer": ({"messages": DONE, "tools": TOOLS}, OFF),
    "reasoning-tool-call": ({"messages": FIRST, "tools": TOOLS, "max_tokens": 2048}, ON),
    "reasoning-answer": ({"messages": DONE, "tools": TOOLS, "max_tokens": 2048}, ON),
    "truncated": ({"messages": FIRST, "tools": TOOLS, "max_tokens": 6}, OFF),
    "no-tools-answer": ({"messages": [SYSTEM, {"role": "user", "content": "Say hello in three words."}]}, OFF),
}


def expect(response: dict) -> dict:
    """The ``Reply`` a transport must read from ``response``, from its fields."""
    choices = response.get("choices") or [{}]
    message = choices[0].get("message") or {}
    calls, invalid = [], []
    for c in message.get("tool_calls") or ():
        arguments = c["function"].get("arguments") or "{}"
        try:
            args = json.loads(arguments)
        except ValueError:
            args = None
        if isinstance(args, dict):
            calls.append([c["id"], c["function"]["name"], args])
        else:
            invalid.append([c["id"], c["function"]["name"], arguments])
    usage = response.get("usage")
    if usage is not None:
        prompt = usage.get("prompt_tokens_details") or {}
        completion = usage.get("completion_tokens_details") or {}
        usage = [
            usage.get("prompt_tokens") or 0,
            usage.get("completion_tokens") or 0,
            prompt.get("cached_tokens"),
            completion.get("reasoning_tokens"),
        ]
    return {
        "content": message.get("content") or "",
        "tool_calls": calls,
        "invalid": invalid,
        "reasoning": message.get("reasoning") or message.get("reasoning_content") or None,
        "usage": usage,
        "finish_reason": choices[0].get("finish_reason"),
    }


def fixture(source: str, request: dict, response: dict) -> dict:
    return {"source": source, "request": request, "response": response, "expect": expect(response)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("url", help="the server's root, without /v1")
    parser.add_argument("--server", required=True, help='its name and version, as "vLLM 0.19.1"')
    parser.add_argument("--prefix", required=True, help="the fixtures' file names start with it")
    parser.add_argument("--about", default="", help="the model and the server's options")
    parser.add_argument("--out", type=Path, default=HERE)
    a = parser.parse_args()
    model = httpx.get(f"{a.url}/v1/models", timeout=30).json()["data"][0]["id"]
    day = time.strftime("%Y-%m-%d")
    source = f"recorded from {a.server}'s OpenAI-compatible server ({a.about or model}), {day}"
    for name, (body, thinking) in CASES.items():
        request = {
            "model": model,
            "temperature": 0,
            **body,
            "chat_template_kwargs": {"enable_thinking": thinking},
        }
        r = httpx.post(f"{a.url}/v1/chat/completions", json=request, timeout=300)
        r.raise_for_status()
        path = a.out / f"{a.prefix}-{name}.json"
        path.write_text(json.dumps(fixture(source, request, r.json()), indent=1) + "\n")
        print(path.name, json.dumps(expect(r.json()))[:160], flush=True)


if __name__ == "__main__":
    main()
