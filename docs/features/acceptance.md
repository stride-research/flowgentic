# Acceptance

In an unattended campaign, such as a batch or HPC run with no human in the
loop, nobody reads an agent's proposal before it becomes HPC work. A rejected
proposal costs one inference. An accepted bad one costs allocation hours.
`flowgentic.acceptance.accept` puts explicit criteria between the two:

- it asks a producer for a value;
- it runs the application's checks on it, in order;
- it asks again, with the reasons, within an attempt budget;
- it falls back to a deterministic value when no proposal passes, or stops
  with every attempt attached.

`accept` depends on Pydantic and the OpenTelemetry API only. The producer can
use LangChain, PydanticAI, a provider's SDK or an AsyncFlow task.

## Rejection is not failure

A call can **fail**: it raises, or times out, and produces no output. Or it can
**succeed with an output that is not acceptable**. `accept` handles the second
case only:

- A check or the producer **rejects** an output by raising `Rejected`, or a
  Pydantic `ValidationError`. `accept` records the rejection and, while
  attempts remain, asks again.
- Any other exception is a **failure**. It propagates at once, with no further
  attempt. The application decides whether to fall back, for example to
  another model when a provider is down.

Retrying a failed call is the job of the layer that can tell whether the
failure is transient: the provider's SDK for a model call, AsyncFlow or the
application for a task. See [Retries and timeouts](../guides/retries-and-timeouts.md).
Do not wrap `accept` in a task that also retries, or two loops handle one
concern.

## The layered gate

Sending the same request again after a rejection is a gamble: at temperature 0
it may return the same answer. So the cheapest and most reliable checks come
first, and each one either gives the model a precise reason or hands over to a
deterministic fallback.

| Layer | What it checks | Where it lives |
| --- | --- | --- |
| 1. Shape | The output matches a schema | The provider's structured outputs: OpenAI's strict JSON schema, Anthropic's structured outputs and strict tool use, vLLM's structured outputs for models on the allocation |
| 2. Schema | Bounds, lengths, uniqueness | Pydantic validation, in the producer |
| 3. Domain | Deterministic predicates on the values | `checks` |
| 4. Verifiers | The value works, where an executable verifier exists, such as a cheap simulation or a solver | An async check, which can submit an AsyncFlow task |
| 5. Soft signals | LLM-as-judge scores, votes | Recorded as scores; they do not gate |

Why this order:

- **Structured outputs guarantee the schema, not the values.** They do not
  hold when the model refuses or the answer is truncated, so the producer
  checks the refusal and the finish reason. Strict formats can also lower
  reasoning quality (Tam et al., 2024). Let the model reason first, for
  example with a rationale field ahead of the answer fields.
- **Deterministic checks are the feedback that self-correction needs.**
  Self-correction works where reliable external feedback is available (Kamoi
  et al., 2024). Without such feedback, a model asked to correct its own
  reasoning does not improve (Huang et al., 2024).
- **A verifier keeps paying off.** Repeated sampling with a verifier keeps
  improving coverage; without one, majority voting and reward models level off
  (Brown et al., 2024).
- **Soft signals are biased and cost an inference each.** LLM judges show
  position, verbosity and self-enhancement biases (Zheng et al., 2023), and a
  vote's accuracy can rise and then fall as calls are added (Chen et al., 2024).
  An unattended campaign should not gate on them.

Every attempt, accepted or rejected, is provenance: the output, which check
rejected it and why, and the final disposition. The application's hard policy
keeps authority over what runs.

## Use it

The agentic AI-HPC demo's planner proposes a batch of simulation candidates.
Its node asks for a batch, checks it, asks once more with the reasons, and
otherwise runs the surrogate's batch:

```python
--8<-- "examples/ai-hpc-coupling/agentic_application.py:planner-accept"
```

The checks are plain functions, cheapest first. Each raises `Rejected` with a
reason the planner can act on:

```python
--8<-- "examples/ai-hpc-coupling/agent_contracts.py:planner-checks"
```

The result is an `Outcome`:

| Field | Content |
| --- | --- |
| `value` | The accepted proposal, or the fallback's value |
| `disposition` | `"accepted"` or `"fallback"` |
| `attempts` | Every `Attempt`, in order: its `value`, `rejected_by` (the check's name, or the stage the producer named) and `reason` |

The demo's agent trace records the disposition and each rejected attempt, and
the runner prints a count of them.

### Semantics

- **`attempts` is the only budget**, and it defaults to 1: no re-ask unless
  asked. Token and cost caps belong to the model client, such as PydanticAI's
  `UsageLimits` or LangChain's `ModelCallLimitMiddleware`, or to the campaign.
  Token and cost totals come from [telemetry](telemetry.md).
- **Checks run in order and stop at the first rejection**, so an expensive
  verifier never sees a proposal that a cheap check rejects. A check can be
  sync or async.
- **The producer receives every earlier attempt**, `()` on the first call, and
  builds the re-ask itself. Flowgentic knows no prompts. The demo's decision
  model adds each rejected answer and a message giving the reason, as
  Instructor does.
- **The producer rejects output it cannot use** by raising
  `Rejected(reason, check=...)`, which names the stage. The demo's decision
  model rejects an answer that does not parse (`"schema"`), a refusal
  (`"refusal"`) and a truncated answer (`"truncation"`):

    ```python
    --8<-- "examples/ai-hpc-coupling/agentic_support.py:decision-rejections"
    ```

- **The fallback is not checked.** It must be valid by construction. The demo's
  fallback runs the surrogate's batch, or an evenly spaced one if the
  surrogate's fails the checks. If the bounds are too narrow for that one too,
  it raises `ValueError`, and `accept` propagates it as a failure, so the
  campaign stops before an unchecked batch runs.
- **No fallback means `NotAccepted`**, raised once the attempts run out, with
  every attempt in its `attempts`.
- **What stays outside the checks:** normalising a value, such as rounding,
  goes in the producer or the schema; a policy that overrides a valid value,
  such as a hard stop, goes after `accept`, in application code.

## Telemetry

Each call of `accept` is recorded with OpenTelemetry's names where they exist,
and Flowgentic's `flowgentic.accept.*` attributes where they do not:

| Record | Attributes |
| --- | --- |
| Span `accept <name>` (INTERNAL) | `flowgentic.accept.name`; `flowgentic.accept.attempts.max`, the budget; `flowgentic.accept.attempts.used`; `flowgentic.accept.disposition`: `accepted`, `fallback` or `not_accepted`. On `NotAccepted` or a failure, the status is ERROR and `error.type` is set |
| Span `accept.attempt <name>`, one per attempt, a child of `accept` | `flowgentic.accept.attempt`, from 1. A rejected attempt leaves the status unset, because a rejection is not a failure |
| Event `gen_ai.evaluation.result`, one per check that ran, and one when the producer rejects its own output | `gen_ai.evaluation.name`, the check or stage; `gen_ai.evaluation.score.label`, `pass` or `fail`; `gen_ai.evaluation.explanation`, the reason; `gen_ai.response.id`, when the value is a LangChain message that carries the provider's id |

The producer's own spans, such as its model calls and the AsyncFlow task it
submits, nest under the attempt's span. A provider SDK's own retries stay
inside one model-call span; a re-ask is a new inference, so it gets a new one.

The evaluation events are log records, emitted through OpenTelemetry's Logs
API while the attempt's span is current, so they carry its trace and span ids.
OpenTelemetry is deprecating span events in favour of these. An application
sees them only once it configures a `LoggerProvider`; the AI-HPC demo's
tracing does, and writes them to the same file as its spans. See
[Telemetry](telemetry.md).

A rejected value is recorded, as JSON in `flowgentic.accept.value` on its
attempt's span, only when `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`
is `SPAN_ONLY` or `SPAN_AND_EVENT`, the switch that OpenTelemetry's GenAI
instrumentation reads. The `Outcome` always holds it, so the application can
keep it with its own record. The reason is always recorded, so a check should
not put anything in it that must stay private.

## Not in `accept`

- Voting, LLM-as-judge and fallback models. A producer may implement any of
  them.
- A helper that builds LangChain re-ask messages. The demo writes its own, in
  `agentic_support.py`.

## Sources

Read on 2026-10-23.

- OpenAI, [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
  Anthropic, [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).
  vLLM, [Structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs.html).
- PydanticAI, [Output](https://pydantic.dev/docs/ai/core-concepts/output/) and
  [Agents](https://pydantic.dev/docs/ai/core-concepts/agents/). Instructor,
  [Retrying](https://python.useinstructor.com/concepts/retrying/). LangChain,
  [Structured output](https://docs.langchain.com/oss/python/langchain/structured-output)
  and [Built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in).
- OpenTelemetry, [GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai):
  the GenAI events, spans and agent spans.
- Tam et al., [Let Me Speak Freely?](https://arxiv.org/abs/2408.02442), EMNLP
  2024 Industry Track.
- Huang et al., [Large Language Models Cannot Self-Correct Reasoning Yet](https://arxiv.org/abs/2310.01798),
  ICLR 2024.
- Kamoi et al., [When Can LLMs Actually Correct Their Own Mistakes?](https://arxiv.org/abs/2406.01297),
  TACL 2024.
- Brown et al., [Large Language Monkeys](https://arxiv.org/abs/2407.21787), 2024.
- Chen et al., [Are More LLM Calls All You Need?](https://arxiv.org/abs/2403.02419),
  2024.
- Zheng et al., [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://arxiv.org/abs/2306.05685),
  NeurIPS 2023 Datasets and Benchmarks.

## API

[`flowgentic.acceptance`](../api/flowgentic/acceptance.md)
