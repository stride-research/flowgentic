"""HTTPTransport, the default transport to an OpenAI-compatible server: one
httpx POST per call, the reply read with ``parse_completion``. Its contract
over the recorded and hand-made completions, the request it sends, its
connection pools, the errors it raises, and the fleet's Recovery waiting out a
server through it. No openai SDK."""

import asyncio
import json
import re
import socket
import ssl
from pathlib import Path

import httpx
import pytest

from flowgentic.fleet import (
    AuthenticationFailed,
    Fleet,
    HTTPTransport,
    ModelServerError,
    Recovery,
    RequestRejected,
    ServerUnavailable,
    Reply,
    ToolCall,
    Transport,
    Usage,
    parse_completion,
)

COMPLETIONS = sorted((Path(__file__).parent / "completions").glob("*.json"))
URL = "http://node-1:8000/v1"
HI = [{"role": "user", "content": "hi"}]
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Look a candidate up.",
            "parameters": {
                "type": "object",
                "properties": {"candidate": {"type": "string"}},
            },
        },
    }
]


def load(path):
    return json.loads(path.read_text())


ANSWER = load(COMPLETIONS[0])["response"]


def mocked(handle):
    """An httpx client whose requests go to ``handle``, as a server's would."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


def recorded(responses, requests):
    """A server that answers each request with the next response, and keeps the
    requests."""
    answers = iter(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=next(answers))

    return mocked(handle)


# --- the contract ------------------------------------------------------------------


@pytest.mark.parametrize("path", COMPLETIONS, ids=lambda p: p.stem)
async def test_each_completion_reads_the_same_through_the_transport(path):
    """The recorded and hand-made completions, served over HTTP, read into the
    ``Reply`` that each file expects."""
    case = load(path)
    transport = HTTPTransport(
        "m", base_url=URL, client=recorded([case["response"]], [])
    )
    reply = await transport.complete(HI)
    expect = case["expect"]
    usage = expect["usage"]
    assert reply.content == expect["content"]
    assert reply.tool_calls == tuple(ToolCall(*c) for c in expect["tool_calls"])
    assert [(c.id, c.name, c.arguments) for c in reply.invalid_tool_calls] == [
        tuple(c) for c in expect["invalid"]
    ]
    assert reply.reasoning == expect["reasoning"]
    assert reply.usage == (None if usage is None else Usage(*usage))
    assert reply.finish_reason == expect["finish_reason"]
    assert reply.raw == case["response"]


async def test_the_transport_sends_the_tools_and_the_calls_keywords(monkeypatch):
    """A call's keywords override the transport's; ``extra_body`` merges into
    the body and ``extra_headers`` into the headers, as the openai SDK does;
    the key is a bearer token."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reply_json = load(
        Path(__file__).parent / "completions" / "vllm-reasoning-tool-call.json"
    )["response"]
    requests = []
    transport = HTTPTransport(
        "Qwen3-8B",
        base_url=URL + "/",
        api_key="sk-local",
        client=recorded([reply_json, reply_json], requests),
        temperature=0,
        max_tokens=64,
    )
    assert isinstance(transport, Transport)
    messages = [{"role": "user", "content": "Find the best temperature for cand-000001."}]
    reply = await transport.complete(
        messages,
        TOOLS,
        max_tokens=512,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}},
        extra_headers={"X-Campaign": "c1"},
    )
    assert reply.tool_calls == (
        ToolCall("chatcmpl-tool-9a1e", "lookup", {"candidate": "cand-000001"}),
    )
    first = requests[0]
    assert str(first.url) == "http://node-1:8000/v1/chat/completions"
    assert first.method == "POST"
    assert first.headers["authorization"] == "Bearer sk-local"
    assert first.headers["x-campaign"] == "c1"
    sent = json.loads(first.content)
    assert sent["model"] == "Qwen3-8B" and sent["messages"] == messages
    assert sent["tools"] == TOOLS
    assert sent["temperature"] == 0 and sent["max_tokens"] == 512  # the call's own wins
    assert sent["chat_template_kwargs"] == {"enable_thinking": True}  # top level
    assert "extra_body" not in sent and "extra_headers" not in sent
    await transport.complete(messages)  # no tools: none sent
    second = json.loads(requests[1].content)
    assert "tools" not in second and second["max_tokens"] == 64
    assert "x-campaign" not in requests[1].headers
    assert transport.base_url == URL
    assert repr(transport) == f"HTTPTransport('Qwen3-8B', base_url={URL!r})"


def test_the_transport_makes_its_client(monkeypatch):
    """Its key ``EMPTY``, as vLLM and SGLang take when they set none, unless
    given one; a timeout of 600 s, as the openai SDK's; a pool per 8 calls in
    flight, the cheapest per call of 4, 8, 16, 32 and 64 at 64 calls in
    flight, and within 3% of the cheapest at 512."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    transport = HTTPTransport("m", base_url=URL)
    assert transport.calls_per_pool == 8 and transport.pools is None
    (client,) = transport.clients
    assert client.headers["authorization"] == "Bearer EMPTY"
    # a server that does not take the connection fails in 5 s, as with the SDK
    assert client.timeout == httpx.Timeout(600.0, connect=5.0)
    transport = HTTPTransport("m", base_url=URL, timeout=2)
    assert transport.clients[0].timeout == httpx.Timeout(2.0, connect=2.0)
    transport = HTTPTransport("m", base_url=URL, timeout=None)  # waits for ever
    assert transport.clients[0].timeout == httpx.Timeout(None, connect=5.0)


def test_only_openais_own_api_gets_the_openai_key_from_the_environment(monkeypatch):
    """``OPENAI_API_KEY`` is OpenAI's: a server on an allocation, often over
    plain HTTP, must never be sent it."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    for url in (URL, "https://openrouter.ai/api/v1", "http://api.openai.com.example:8000/v1"):
        transport = HTTPTransport("m", base_url=url)
        assert transport.clients[0].headers["authorization"] == "Bearer EMPTY", url
    openai = HTTPTransport("gpt-5", base_url="https://api.openai.com/v1")
    assert openai.clients[0].headers["authorization"] == "Bearer sk-env"
    given = HTTPTransport("m", base_url=URL, api_key="sk-given")
    assert given.clients[0].headers["authorization"] == "Bearer sk-given"


# --- errors ------------------------------------------------------------------------


async def test_a_given_client_keeps_its_own_settings_unless_told_otherwise():
    """A client the caller gives keeps its timeout and its Authorization,
    unless the transport is given a timeout or a key of its own."""
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers.get("authorization"), request.extensions["timeout"]))
        return httpx.Response(200, json=ANSWER)

    def client():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handle),
            headers={"Authorization": "Bearer MINE"},
            timeout=7.0,
        )

    own = HTTPTransport("m", base_url=URL, client=client())
    await own.complete(HI)
    await HTTPTransport("m", base_url=URL, client=client(), timeout=30, api_key="k").complete(HI)
    (mine, mine_timeout), (given, given_timeout) = seen
    assert mine == "Bearer MINE" and mine_timeout["read"] == 7.0
    assert own.timeout == httpx.Timeout(7.0)  # what the calls get
    assert given == "Bearer k" and given_timeout["read"] == 30 and given_timeout["connect"] == 5.0


async def test_a_bare_given_client_gets_the_transports_timeout():
    """A client left at httpx's default timeout, 5 s, gets the transport's 600 s,
    as the openai SDK does with a bare http_client: a model call takes longer
    than 5 s."""
    seen = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json=ANSWER)

    bare = HTTPTransport("m", base_url=URL, client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    await bare.complete(HI)
    assert bare.timeout == 600.0
    assert seen == [{"connect": 5.0, "read": 600.0, "write": 600.0, "pool": 600.0}]


async def test_all_its_pools_share_one_ssl_context_and_close(monkeypatch):
    """Each pool would otherwise build an SSL context of its own, on the event
    loop; ``aclose`` closes the clients the transport made, once their calls
    have ended, and never a client it was given."""
    pools = Pools(monkeypatch)  # records each made client's options
    transport = HTTPTransport("m", base_url="https://node-1:8443/v1", calls_per_pool=2)
    calls = [asyncio.create_task(transport.complete(HI)) for _ in range(5)]
    while pools.now() < 5:
        await asyncio.sleep(0)
    contexts = {id(k["verify"]) for k in pools.kwargs}
    assert len(pools.kwargs) == 3 and len(contexts) == 1
    assert isinstance(pools.kwargs[0]["verify"], ssl.SSLContext)
    await transport.aclose()  # calls in flight: they end first
    pools.gate.set()
    await asyncio.gather(*calls)
    await asyncio.sleep(0)
    assert all(c.is_closed for c in transport.clients)
    given = mocked(lambda request: httpx.Response(200, json=ANSWER))
    alone = HTTPTransport("m", base_url=URL, client=given)
    await alone.aclose()
    assert not given.is_closed


async def test_recovery_closes_the_transport_it_replaces():
    """A replica that Recovery replaces, with a new address or key, is closed,
    so its pools and their SSL contexts do not pile up."""
    port = free_port()
    old = HTTPTransport("m", base_url=f"http://127.0.0.1:{free_port()}/v1")
    servers = []

    async def check(i):
        if not servers:
            servers.append(await answering(port, ANSWER))
        return HTTPTransport("m", base_url=f"http://127.0.0.1:{port}/v1")

    fleet = Fleet([old], recovery=Recovery(check, poll_s=0.02))
    try:
        await asyncio.wait_for(fleet.ainvoke("a", HI), 5)
        for _ in range(20):
            if all(c.is_closed for c in old.clients):
                break
            await asyncio.sleep(0.01)
    finally:
        for server in servers:
            server.close()
            await server.wait_closed()
    assert fleet._core.replicas[0] is not old
    assert all(c.is_closed for c in old.clients)


def test_a_base_url_must_be_an_http_url():
    """A ``base_url`` without a scheme is a set-up error, refused when the
    transport is made, not a server down for ``Recovery`` to wait out."""
    for url in ("127.0.0.1:9/v1", "localhost:8000/v1", "node-1/v1", "ftp://node-1/v1"):
        with pytest.raises(ValueError, match="base_url"):
            HTTPTransport("m", base_url=url)


@pytest.mark.parametrize(
    "exc",
    [
        httpx.UnsupportedProtocol("no scheme"),
        httpx.DecodingError("bad gzip"),
        httpx.TooManyRedirects("loop"),
        httpx.LocalProtocolError("bad header"),
    ],
    ids=["protocol", "decoding", "redirects", "local-protocol"],
)
async def test_a_request_httpx_cannot_make_or_read_is_not_transient(exc):
    with pytest.raises(ModelServerError) as raised:
        await HTTPTransport("m", base_url=URL, client=raising(exc)).complete(HI)
    assert raised.value.transient is False and type(raised.value) is ModelServerError
    assert isinstance(raised.value.__cause__, httpx.RequestError)


@pytest.mark.parametrize(
    ("body", "kind", "code"),
    [
        ({"object": "error", "message": "boom", "code": 400}, RequestRejected, 400),
        ({"choices": []}, ModelServerError, None),
        ({"id": "x"}, ModelServerError, None),
        ({"error": {"code": 429, "message": "boom: rate limited upstream"}}, ServerUnavailable, 429),
        ({"error": {"code": "502", "message": "boom: provider down"}}, ServerUnavailable, 502),
        ({"error": {"code": 401, "message": "boom: key"}}, AuthenticationFailed, 401),
    ],
    ids=["vllm-error", "no-choices", "nothing", "openrouter-429", "openrouter-502", "401"],
)
async def test_a_completion_without_choices_is_an_error_not_an_empty_answer(body, kind, code):
    """An error object with status 200, or a completion with no choices, would
    otherwise end the agent with an empty answer. Its ``code``, as OpenRouter
    sends one, is read as an HTTP status: busy or a 5xx is transient."""
    with pytest.raises(ModelServerError, match="no choices") as raised:
        await HTTPTransport("m", base_url=URL, client=recorded([body], [])).complete(HI)
    error = raised.value
    assert type(error) is kind and error.body == body and error.status_code == code
    assert error.transient is (kind in (ServerUnavailable, AuthenticationFailed))
    if "message" in str(body):
        assert "boom" in str(error)
    with pytest.raises(kind, match="no choices"):
        parse_completion(body)


async def test_a_closed_transport_refuses_calls_with_its_own_error():
    """After ``aclose()`` a call is a ``ModelServerError``, not transient, not
    httpx's own ``RuntimeError``; on a given client too, which it leaves open."""
    made = HTTPTransport("m", base_url=URL)
    given = HTTPTransport("m", base_url=URL, client=mocked(lambda request: httpx.Response(200, json=ANSWER)))
    for transport in (made, given):
        await transport.aclose()
        with pytest.raises(ModelServerError, match="closed") as raised:
            await transport.complete(HI)
        assert raised.value.transient is False
    assert not given.clients[0].is_closed


async def test_recovery_closes_a_replaced_replica_whose_aclose_is_sync():
    """A transport of one's own may close synchronously."""

    class Closing:
        model, base_url, closed = "m", None, False

        def __init__(self, fail):
            self.fail = fail

        async def complete(self, messages, tools=None, **kwargs):
            if self.fail:
                raise ServerUnavailable("down")
            return Reply("ok")

        def aclose(self):
            self.closed = True

    old = Closing(fail=True)

    async def check(i):
        return Closing(fail=False)

    fleet = Fleet([old], recovery=Recovery(check, poll_s=0.02))
    reply = await asyncio.wait_for(fleet.ainvoke("a", HI), 5)
    assert reply.content == "ok" and old.closed


@pytest.mark.parametrize(
    ("headers", "says"),
    [({"X-Bad": "a\nb"}, "header"), ({"X-Bad": "é"}, "header")],
    ids=["newline", "non-ascii"],
)
async def test_a_header_httpx_cannot_send_is_a_set_up_error(headers, says):
    """Not a server that is down: nothing a wait mends."""
    transport = HTTPTransport("m", base_url=URL, client=mocked(lambda request: httpx.Response(200, json=ANSWER)))
    with pytest.raises(ModelServerError, match=says) as raised:
        await transport.complete(HI, extra_headers=headers)
    assert raised.value.transient is False


async def test_a_lone_surrogate_does_not_stop_the_conversation():
    """An undecodable file name, as Linux gives one, reaches the server
    escaped, as ``ensure_ascii`` would escape it, instead of failing every
    later call with ``UnicodeEncodeError``."""
    name = b"run-\xff.dat".decode("utf-8", "surrogateescape")
    requests = []
    transport = HTTPTransport("m", base_url=URL, client=recorded([ANSWER], requests))
    await transport.complete([{"role": "user", "content": f"look at {name}"}])
    assert "\\udcff" in requests[0].content.decode("utf-8")
    arguments = Reply("", (ToolCall("a", "ls", {"path": name}),)).message()["tool_calls"][0]["function"]["arguments"]
    arguments.encode("utf-8")  # sendable
    assert "\\udcff" in arguments


async def test_a_401_without_a_key_says_where_the_environments_key_goes(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    transport = HTTPTransport("m", base_url=URL, client=failing(401, {"error": {"message": "no key"}}))
    with pytest.raises(AuthenticationFailed, match="OPENAI_API_KEY is sent to api.openai.com only"):
        await transport.complete(HI)
    keyed = HTTPTransport("m", base_url=URL, api_key="k", client=failing(401, {"error": {"message": "bad key"}}))
    with pytest.raises(AuthenticationFailed) as raised:
        await keyed.complete(HI)
    assert "OPENAI_API_KEY" not in str(raised.value)


def test_the_conversation_keeps_text_as_written():
    """Arguments and text reach the model as written, not escaped to ASCII,
    as LangChain's ToolNode and langchain-openai send them."""
    reply = Reply("Grüße", (ToolCall("a", "city", {"name": "Zürich ✓"}),))
    assert reply.message()["tool_calls"][0]["function"]["arguments"] == '{"name": "Zürich ✓"}'
    parsed = parse_completion({"choices": [{"message": {"content": [{"type": "text", "text": "Zürich"}]}}]})
    assert "Zürich" in parsed.content


def test_an_empty_reasoning_field_does_not_hide_reasoning_content():
    completion = {"choices": [{"message": {"content": "x", "reasoning": "", "reasoning_content": "think"}}]}
    assert parse_completion(completion).reasoning == "think"


def failing(status, body):
    def handle(request: httpx.Request) -> httpx.Response:
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    return mocked(handle)


def raising(exc):
    def handle(request: httpx.Request) -> httpx.Response:
        raise exc

    return mocked(handle)


@pytest.mark.parametrize(
    ("client", "kind", "status", "says"),
    [
        (raising(httpx.ConnectError("connection refused")), ServerUnavailable, None, "connection refused"),
        (raising(httpx.ReadTimeout("timed out")), ServerUnavailable, None, "ReadTimeout"),
        (raising(httpx.RemoteProtocolError("disconnected")), ServerUnavailable, None, "disconnected"),
        (failing(503, {"error": {"message": "overloaded", "type": "ServiceUnavailable"}}), ServerUnavailable, 503, "overloaded"),
        (failing(500, "Internal Server Error"), ServerUnavailable, 500, "Internal Server Error"),
        (failing(401, {"error": {"message": "invalid key", "code": 401}}), AuthenticationFailed, 401, "invalid key"),
        (
            failing(400, {"object": "error", "message": "This model's maximum context length is 32768 tokens.", "code": 400}),
            RequestRejected,
            400,
            "maximum context length",
        ),
        (failing(404, {"detail": "Not Found"}), RequestRejected, 404, "Not Found"),
        (failing(422, {"detail": "bad tool schema"}), RequestRejected, 422, "bad tool schema"),
        (failing(429, {"error": "rate limited"}), ServerUnavailable, 429, "rate limited"),
        (failing(408, "Request Timeout"), ServerUnavailable, 408, "Request Timeout"),
        (failing(409, {"error": {"message": "try again"}}), ServerUnavailable, 409, "try again"),
    ],
    ids=["refused", "timeout", "dropped", "503", "500", "401", "400", "404", "422", "429", "408", "409"],
)
async def test_a_failed_call_raises_flowgentics_error_with_the_servers_message(client, kind, status, says):
    """No answer, a timeout, a 5xx, or a 408, 409 or 429, which the openai
    SDK retries too, is a server that is down or busy, and a 401 one that came
    back with a new key: transient, so a fleet's ``Recovery`` waits for it.
    Any other 4xx is the request's fault."""
    with pytest.raises(kind) as raised:
        await HTTPTransport("m", base_url=URL, client=client).complete(HI)
    error = raised.value
    assert isinstance(error, ModelServerError)
    assert error.status_code == status
    assert says in str(error)
    assert error.transient is (kind is not RequestRejected)
    if status is None:
        assert isinstance(error.__cause__, httpx.TransportError)


async def test_a_reply_that_is_not_json_is_an_error():
    client = failing(200, "<html>a proxy's page</html>")
    with pytest.raises(ModelServerError, match="not a JSON") as raised:
        await HTTPTransport("m", base_url=URL, client=client).complete(HI)
    assert raised.value.transient is False and raised.value.status_code == 200


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def answering(port, completion):
    """A model server on ``port`` that answers every chat completion with
    ``completion``."""

    async def handle(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        length = int(re.search(rb"content-length: *(\d+)", head, re.I).group(1))
        await reader.readexactly(length)
        body = json.dumps(completion).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body
        )
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", port)


async def test_recovery_waits_out_a_server_that_refuses_connections_then_answers():
    """A replica whose server is not up yet refuses the connection: the fleet's
    ``Recovery`` waits until its check finds the server, then sends the call
    again through the same transport."""
    port = free_port()
    transport = HTTPTransport("m", base_url=f"http://127.0.0.1:{port}/v1")
    servers, checks = [], []

    async def check(i):
        checks.append(i)
        if not servers:
            servers.append(await answering(port, ANSWER))  # it comes up now
            return None
        return transport

    fleet = Fleet([transport], recovery=Recovery(check, poll_s=0.02))
    try:
        reply = await asyncio.wait_for(fleet.ainvoke("a", HI), 5)
    finally:
        for server in servers:
            server.close()
            await server.wait_closed()
    assert reply.raw == ANSWER
    assert checks == [0, 0] and fleet._core.retried == 1


async def test_recovery_does_not_wait_out_a_rejected_request():
    checks = []

    async def check(i):
        checks.append(i)
        return None

    client = failing(400, {"object": "error", "message": "bad tool schema"})
    fleet = Fleet(
        [HTTPTransport("m", base_url=URL, client=client)],
        recovery=Recovery(check, poll_s=0.02),
    )
    with pytest.raises(RequestRejected, match="bad tool schema"):
        await fleet.ainvoke("a", HI)
    assert checks == []


# --- connection pools ----------------------------------------------------------------


class Pools:
    """httpx clients on a mock server: each counts its calls and the most it
    held at once; a call is answered once ``gate`` is set."""

    def __init__(self, monkeypatch):
        self.made, self.kwargs = [], []
        self.gate = asyncio.Event()
        real = httpx.AsyncClient

        def make(**kwargs):
            if "transport" in kwargs:  # a test's own client, not the transport's
                return real(**kwargs)
            count = {"calls": 0, "now": 0, "most": 0}

            async def handle(request: httpx.Request) -> httpx.Response:
                count["calls"] += 1
                count["now"] += 1
                count["most"] = max(count["most"], count["now"])
                try:
                    await self.gate.wait()
                finally:
                    count["now"] -= 1
                return httpx.Response(200, json=ANSWER)

            self.made.append(count)
            self.kwargs.append(kwargs)
            return real(transport=httpx.MockTransport(handle), **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", make)

    def now(self):
        return sum(c["now"] for c in self.made)


async def test_the_transport_opens_a_pool_for_every_few_calls_in_flight(monkeypatch):
    """A client's CPU per call grows with the calls its connection pool holds
    in flight; the transport opens another pool when each holds
    ``calls_per_pool`` calls."""
    pools = Pools(monkeypatch)
    transport = HTTPTransport("m", base_url=URL, calls_per_pool=4)
    assert len(pools.made) == 1 and transport.pools is None  # grows as needed
    calls = [asyncio.create_task(transport.complete(HI)) for _ in range(10)]
    while pools.now() < 10:
        await asyncio.sleep(0)
    assert [c["most"] for c in pools.made] == [4, 4, 2]
    pools.gate.set()
    await asyncio.gather(*calls)
    await transport.complete(HI)  # one call in flight: the first pool
    assert [c["calls"] for c in pools.made] == [5, 4, 2]
    # no cap on a pool's connections: a fixed number of pools holds any load
    assert all(k["limits"].max_connections is None for k in pools.kwargs)


async def test_the_transport_spreads_its_calls_over_a_fixed_number_of_pools(monkeypatch):
    pools = Pools(monkeypatch)
    transport = HTTPTransport("m", base_url=URL, pools=3, calls_per_pool=2)
    assert len(pools.made) == 3 and transport.pools == 3
    calls = [asyncio.create_task(transport.complete(HI)) for _ in range(7)]
    while pools.now() < 7:
        await asyncio.sleep(0)
    assert [c["most"] for c in pools.made] == [3, 2, 2]  # the least busy takes each
    pools.gate.set()
    await asyncio.gather(*calls)
    with pytest.raises(ValueError, match="pools"):
        HTTPTransport("m", base_url=URL, pools=0)
    with pytest.raises(ValueError, match="calls_per_pool"):
        HTTPTransport("m", base_url=URL, calls_per_pool=0)
    given = mocked(lambda request: httpx.Response(200, json=ANSWER))
    with pytest.raises(ValueError, match="client"):
        HTTPTransport("m", base_url=URL, client=given, pools=2)
    alone = HTTPTransport("m", base_url=URL, client=given)
    assert alone.clients == [given] and alone.pools == 1  # never grows
