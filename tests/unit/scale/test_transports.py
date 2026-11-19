"""Transports' contract: chat completions of vLLM, SGLang and OpenRouter, as
JSON and as the openai SDK's objects, read into a Reply; the reply as the
conversation keeps it; and OpenAITransport against a server on httpx's mock
transport."""

import json
from pathlib import Path

import httpx
import pytest
from openai import APIConnectionError, AsyncOpenAI
from openai.types.chat import ChatCompletion

from flowgentic.fleet import (
    InvalidToolCall,
    OpenAITransport,
    Reply,
    ToolCall,
    Transport,
    Usage,
    parse_completion,
)

COMPLETIONS = sorted((Path(__file__).parent / "completions").glob("*.json"))


def load(path):
    return json.loads(path.read_text())


def expected(expect):
    usage = expect["usage"]
    return {
        "content": expect["content"],
        "tool_calls": tuple(ToolCall(*c) for c in expect["tool_calls"]),
        "invalid": tuple((c[0], c[1], c[2]) for c in expect["invalid"]),
        "reasoning": expect["reasoning"],
        "usage": None if usage is None else Usage(*usage),
        "finish_reason": expect["finish_reason"],
    }


def seen(reply):
    return {
        "content": reply.content,
        "tool_calls": reply.tool_calls,
        "invalid": tuple((c.id, c.name, c.arguments) for c in reply.invalid_tool_calls),
        "reasoning": reply.reasoning,
        "usage": reply.usage,
        "finish_reason": reply.finish_reason,
    }


def test_there_are_completions_of_each_server():
    names = [p.stem for p in COMPLETIONS]
    for server in ("vllm", "sglang", "openrouter"):
        assert any(n.startswith(server) for n in names), server


@pytest.mark.parametrize("path", COMPLETIONS, ids=lambda p: p.stem)
def test_a_completion_reads_the_same_as_json_and_as_the_sdks_object(path):
    case = load(path)
    want = expected(case["expect"])
    from_json = parse_completion(case["response"])
    assert seen(from_json) == want
    from_sdk = parse_completion(ChatCompletion.model_validate(case["response"]))
    assert seen(from_sdk) == want
    assert from_sdk.id == case["response"]["id"]
    assert from_sdk.model == case["response"]["model"]


@pytest.mark.parametrize("path", COMPLETIONS, ids=lambda p: p.stem)
def test_the_conversation_keeps_the_reply_without_its_reasoning(path):
    reply = parse_completion(load(path)["response"])
    message = reply.message()
    assert message["role"] == "assistant" and message["content"] == reply.content
    assert set(message) <= {"role", "content", "tool_calls"}  # no reasoning, no usage
    calls = message.get("tool_calls", [])
    assert [c["id"] for c in calls] == [c.id for c in reply.tool_calls] + [
        c.id for c in reply.invalid_tool_calls
    ]
    for call in calls:  # every arguments string parses, as servers parse them again
        assert isinstance(json.loads(call["function"]["arguments"]), dict)
    for call, sent in zip(reply.tool_calls, calls, strict=False):
        assert json.loads(sent["function"]["arguments"]) == call.args


def test_a_reply_with_nothing_is_an_empty_answer():
    reply = parse_completion({"choices": []})
    assert reply == Reply() and reply.message() == {"role": "assistant", "content": ""}


def test_an_invalid_call_says_why():
    case = load(Path(__file__).parent / "completions" / "vllm-malformed-arguments.json")
    (invalid,) = parse_completion(case["response"]).invalid_tool_calls
    assert isinstance(invalid, InvalidToolCall)
    assert invalid.error.startswith("not valid JSON: ")


# --- OpenAITransport ---------------------------------------------------------------


def server(responses, requests):
    """An OpenAI-compatible server on httpx's mock transport: answers each
    request with the next response, and keeps the requests' bodies."""
    answers = iter(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(answers))

    return AsyncOpenAI(
        base_url="http://node-1:8000/v1",
        api_key="EMPTY",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Look a candidate up.",
            "parameters": {"type": "object", "properties": {"candidate": {"type": "string"}}},
        },
    }
]


async def test_the_openai_transport_sends_the_tools_and_the_calls_keywords():
    reply_json = load(Path(__file__).parent / "completions" / "vllm-reasoning-tool-call.json")["response"]
    requests = []
    transport = OpenAITransport(
        "Qwen3-8B", client=server([reply_json, reply_json], requests), temperature=0, max_tokens=64
    )
    assert isinstance(transport, Transport)
    messages = [{"role": "user", "content": "Find the best temperature for cand-000001."}]
    reply = await transport.complete(
        messages, TOOLS, max_tokens=512, extra_body={"chat_template_kwargs": {"enable_thinking": True}}
    )
    assert reply.tool_calls == (ToolCall("chatcmpl-tool-9a1e", "lookup", {"candidate": "cand-000001"}),)
    assert reply.reasoning.startswith("The user wants")
    (sent,) = requests[:1]
    assert sent["model"] == "Qwen3-8B" and sent["messages"] == messages
    assert sent["tools"] == TOOLS
    assert sent["temperature"] == 0 and sent["max_tokens"] == 512  # the call's own wins
    assert sent["chat_template_kwargs"] == {"enable_thinking": True}  # extra_body merges in
    await transport.complete(messages)  # no tools: none sent
    assert "tools" not in requests[1] and requests[1]["max_tokens"] == 64
    assert transport.base_url == "http://node-1:8000/v1/"


async def test_the_openai_transport_raises_the_clients_errors():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = AsyncOpenAI(
        base_url="http://node-1:8000/v1",
        api_key="EMPTY",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(down)),
    )
    with pytest.raises(APIConnectionError):
        await OpenAITransport("m", client=client).complete([{"role": "user", "content": "hi"}])


def test_the_openai_transport_makes_its_client(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    transport = OpenAITransport("m", base_url="http://node-2:8000/v1", max_retries=0, timeout=5)
    assert transport.client.api_key == "EMPTY" and transport.client.max_retries == 0
    assert transport.base_url == "http://node-2:8000/v1/"
