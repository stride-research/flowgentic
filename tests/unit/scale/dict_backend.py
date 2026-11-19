"""A backend for the agent loop on OpenAI-format message dicts, with a scripted
model and no LangChain: what the loop's seams allow. For tests only; it
imports nothing of LangChain's."""

import inspect
import json
import typing
from collections.abc import Callable

from pydantic import create_model

from flowgentic.loop._seams import Backend, Call, InlineTool, ModelCall, Spec, text


def schema_of(name: str, fn: Callable) -> type:
    """A pydantic model of ``fn``'s parameters."""
    hints = typing.get_type_hints(fn)
    fields = {
        p.name: (hints.get(p.name, typing.Any), ... if p.default is p.empty else p.default)
        for p in inspect.signature(fn).parameters.values()
    }
    return create_model(name, **fields)


def assistant(content: str = "", calls: tuple = ()) -> dict:
    """A model's reply; ``calls`` are (id, name, arguments) triples, the
    arguments a dict or a string that may not parse."""
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {
                "id": id_,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": args if isinstance(args, str) else json.dumps(args),
                },
            }
            for id_, name, args in calls
        ]
    return message


class DictTool(InlineTool):
    """A plain function, sync or async, as an inline tool."""

    def __init__(self, fn: Callable) -> None:
        self.fn = fn
        self.name = fn.__name__
        self.schema = schema_of(fn.__name__, fn)

    async def run(self, call: Call) -> tuple[dict, bool]:
        args = self.schema.model_validate(call.args)
        out = self.fn(**{k: getattr(args, k) for k in call.args})
        if inspect.isawaitable(out):
            out = await out
        return {"role": "tool", "tool_call_id": call.id, "content": text(out)}, False


class ScriptedCall(ModelCall):
    def __init__(self, script: Callable[[list[dict]], dict]) -> None:
        self.script = script
        self.done: list[str] = []

    async def __call__(self, agent_id: str, messages: list) -> dict:
        return self.script(messages)

    def agent_done(self, agent_id: str) -> None:
        self.done.append(agent_id)


def openai_tool(spec: Spec) -> dict:
    params = spec.schema.model_json_schema() if isinstance(spec.schema, type) else spec.schema
    return {
        "type": "function",
        "function": {"name": spec.name, "description": spec.description, "parameters": params},
    }


class DictBackend(Backend):
    """Messages as OpenAI chat dicts; ``script(messages)`` is the model."""

    def __init__(self, script: Callable[[list[dict]], dict]) -> None:
        self.script = script
        self.bound: list[dict] = []
        self.model: ScriptedCall | None = None

    def to_list(self, value: object) -> list:
        if isinstance(value, str):
            return [{"role": "user", "content": value}]
        if isinstance(value, dict):
            return [dict(value)]
        return [dict(m) for m in value]

    def system(self, text: str) -> dict:
        return {"role": "system", "content": text}

    def human(self, text: str) -> dict:
        return {"role": "user", "content": text}

    def result(self, call: Call, content: str, error: bool) -> dict:
        return {"role": "tool", "tool_call_id": call.id, "content": content}

    def calls(self, message: object) -> list[Call] | None:
        if message.get("role") != "assistant":
            return None
        out = []
        for c in message.get("tool_calls") or []:
            f = c["function"]
            try:
                args, error = json.loads(f["arguments"]), None
            except json.JSONDecodeError as exc:
                args, error = None, f"invalid JSON: {exc}"
            out.append(Call(c["id"], f["name"], args, error, c))
        return out

    def answers(self, message: object) -> str | None:
        return message.get("tool_call_id") if message.get("role") == "tool" else None

    def text(self, message: object) -> str:
        return message.get("content") or ""

    def schema(self, name: str, fn: Callable) -> type:
        return schema_of(name, fn)

    def inline(self, tool: object) -> InlineTool | None:
        return tool if isinstance(tool, DictTool) else None

    def bind(self, tools: list) -> ModelCall:
        self.bound = [
            openai_tool(t if isinstance(t, Spec) else Spec(t.name, t.fn.__doc__ or "", t.schema))
            for t in tools
        ]
        self.model = ScriptedCall(self.script)
        return self.model
