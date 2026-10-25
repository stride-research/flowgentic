# Logging

Flowgentic logs through Python's standard `logging` module, as the
[logging HOWTO](https://docs.python.org/3/howto/logging.html#configuring-logging-for-a-library)
asks of a library. Each module logs to `logging.getLogger(__name__)`, under the
`flowgentic` logger, and importing Flowgentic configures nothing: no level, no
handler, no thread. Without any configuration, Python's last-resort handler
prints warnings and errors to stderr, as bare messages, which the HOWTO calls
the best default; everything else is the application's choice.

## See Flowgentic's records

In a script, or while debugging:

```python
import flowgentic

flowgentic.configure_logging("DEBUG")
```

`configure_logging(level="INFO", *, stream=None)` adds one handler to the
`flowgentic` logger, writing plain text to stderr (or to `stream`), and sets
that logger's level. Calling it again replaces the handler. It never touches the
root logger, and records still propagate: if your application has handlers of
its own, they see Flowgentic's records too, so use them instead of
`configure_logging`, or each record is printed twice.

## Configure it from your application

An application usually configures logging once, for every library it uses:

```python
import logging

logging.basicConfig(level=logging.WARNING)
logging.getLogger("flowgentic").setLevel(logging.INFO)
```

`logging.config.dictConfig` works the same way. Because Flowgentic's records are
standard records, the application's logging tools take them as they are:
structlog renders them through its `ProcessorFormatter`, loguru through an
intercepting handler, and OpenTelemetry's logging handler exports them as
OpenTelemetry logs. With OpenTelemetry's logging instrumentation, each record
also carries the trace and span ids of the call that logged it. Flowgentic needs
no code for any of these.

## Levels

| Level | What Flowgentic logs there |
| --- | --- |
| `DEBUG` | Per-call detail: functions registered and submitted, tasks completed, graph edits |
| `INFO` | Lifecycle: the integration's `WorkflowEngine` started and shut down, the report written |
| `WARNING` | Degraded results (a failed summary, a graph that could not be rendered), and requests refused with `False`, such as a `MutableGraph` edit |
| `ERROR` | Failures |

## What the records leave out

No record carries prompts, model outputs, message lists or graph states, at any
level; a router's choice of agents is logged as a count. A record of a failure
carries the exception's type and message as the code raised them, as
OpenTelemetry's GenAI conventions recommend for exceptions, while they make
message content opt-in. Logs are diagnostics for whoever debugs Flowgentic or
the application; what a run did, such as its decisions, retries and token usage,
belongs to its [telemetry](telemetry.md).