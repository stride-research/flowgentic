# Flowgentice Observability Module

This module provides unified observability (logging, tracing, metrics) for Flowgentice using the [OBSERVE](https://github.com/stride-research/observe) package.

## Quick Start

```python
import asyncio
from flowgentic.utils.observability import (
    initialize_flowgentice_observability,
    get_flowgentice_logger,
    flush_all_data,
)

async def main():
    # Initialize (auto-detects HPC/dev/testing environment)
    await initialize_flowgentice_observability("my_app")
    
    # Get a logger
    logger = get_flowgentice_logger("my_component")
    logger.info("Processing started", extra={"batch_id": 123})
    
    # Flush before exit
    await flush_all_data()

asyncio.run(main())
```

## Features

- **Unified API**: Single interface for logs, traces, and metrics
- **Async-First**: Non-blocking I/O for high performance
- **Environment-Aware**: Auto-optimizes for HPC, development, and testing
- **High-Performance**: Parquet format for traces and metrics
- **Backward Compatible**: Works with existing Logger API

## API Reference

### Initialization

```python
await initialize_flowgentice_observability(
    application_name: str = "flowgentice",
    output_dir: str = "./observability_data",
    preset: Optional[str] = None,  # Auto-detect or override
    **kwargs
)
```

### Logging

```python
# Get a logger
logger = get_flowgentice_logger("component_name")

# Use standard logging methods
logger.info("Message", extra={"key": "value"})
logger.debug("Debug message")
logger.warning("Warning message")
logger.error("Error message")

# Or use async API directly
await record_log("event_name", {"key": "value"})
```

### Tracing

```python
# Get a tracer
tracer = get_flowgentice_tracer("service_name")

# Use span context manager
with tracer.start_span("operation_name") as span:
    span.add_attribute("key", "value")
    # Your code here

# Or use async API directly
await record_trace(
    "operation_name",
    duration=1.5,
    attributes={"key": "value"}
)
```

### Metrics

```python
# Record metrics directly
await record_metric("counter_name", 1, metric_type="counter")
await record_metric("gauge_name", 42, metric_type="gauge")
await record_metric("histogram_name", 1.5, metric_type="histogram")

# With labels
await record_metric(
    "http_requests_total",
    1,
    metric_type="counter",
    labels={"method": "GET", "endpoint": "/api"}
)
```

### Cleanup

```python
# Always flush before application exit
await flush_all_data()
```

## Environment Detection

The module automatically detects your environment:

- **HPC**: Detects SLURM_JOB_ID, PBS_JOBID, LSB_JOBID → uses "hpc" preset
- **Testing**: Detects pytest/CI → uses "debugging" preset
- **Development**: Default → uses "development" preset

Override with `preset` parameter:

```python
await initialize_flowgentice_observability("my_app", preset="production")
```

## Output Structure

```
observability_data/
├── workflow_<session_id>/
│   ├── logs.jsonl          # Streaming logs (JSONL)
│   ├── traces.parquet      # High-performance traces
│   └── metrics.parquet     # High-performance metrics
```

## Configuration Options

### Presets

- `development`: Human-readable, immediate flushing, verbose
- `production`: Minimal overhead, efficient formats
- `hpc`: Optimized for shared filesystems, large batches
- `debugging`: Fast startup, minimal output

### Custom Configuration

```python
config = await initialize_flowgentice_observability(
    "my_app",
    output_dir="./custom_dir",
    preset="production",
    logs_format="json",
    traces_format="parquet",
    metrics_format="parquet",
)
```

## Backward Compatibility

For existing code using the old Logger:

```python
from flowgentic.utils.observability.compat import FlowgenticeLogger

logger = FlowgenticeLogger(colorful_output=True)
```

## Examples

See `tests/test_observability_integration.py` for comprehensive examples.

## Documentation

- [OBSERVE Documentation](https://stride-research.github.io/observe/)
- [Integration Guide](../../../docs/observability_integration.md)
- [Full Integration Plan](/tmp/observe_flowgentice_integration_plan.md)
