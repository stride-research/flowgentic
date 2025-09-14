# Flowgentic

A lightweight integration library that integrates [Radical Asyncflow](https://github.com/radical-cybertools/radical.asyncflow) with agentic frameworks such as [Academy](https://github.com/proxystore/academy) and [Langgraph](https://github.com/langchain-ai/langgraph) to enable running agentic workflows on HPC systems.

## Overview

Flowgentic provides two main integration layers:

- **Academy Integration**: Use Academy agents as AsyncFlow workflow tasks
- **LangGraph Integration**: Expose AsyncFlow tasks as LangChain tools with fault tolerance

## Installation

### Using uv (Recommended)

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install the package
uv add flowgentic
```

### Development Setup with uv

```bash
git clone https://github.com/your-username/flowgentic.git
cd flowgentic

# Create virtual environment and install dependencies
uv sync

# Activate the virtual environment
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### Common uv Workflows

```bash
# Add a new dependency
uv add requests

# Add a development dependency
uv add --dev pytest

# Update lockfile after dependency changes
uv lock

# Sync dependencies (install from lockfile)
uv sync

# Install in development mode
uv pip install -e .

# Run scripts defined in pyproject.toml
uv run ruff check
uv run ruff format
```

### Linting and Formatting

This project uses [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
# Check for linting issues
uv run ruff check

# Fix linting issues automatically
uv run ruff check --fix

# Format code
uv run ruff format

# Check formatting without changes
uv run ruff format --check
```

### Traditional pip (Alternative)

```bash
git clone https://github.com/your-username/flowgentic.git
cd flowgentic
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Academy Integration

Bridge Academy agents with AsyncFlow workflows.

### Basic Usage

```python
import asyncio
from academy.agent import Agent, action, loop
from radical.asyncflow import WorkflowEngine, ThreadExecutionBackend
from flowgentic.academy import AcademyIntegration

class Counter(Agent):
    count: int = 0

    @loop
    async def increment(self, shutdown: asyncio.Event):
        while not shutdown.is_set():
            await asyncio.sleep(0.1)
            self.count += 1

    @action
    async def get_count(self) -> int:
        return self.count

async def main():
    # Setup AsyncFlow
    backend = ThreadExecutionBackend({})
    backend.set_main_loop()
    flow = WorkflowEngine(backend=backend)

    # Use Academy agents in workflows
    async with AcademyIntegration(flow) as integration:
        # Create a task from agent action
        counter_task = integration.create_agent_task(Counter, 'get_count')

        @flow.function_task
        async def get_counter_value():
            return await counter_task()

        # Execute workflow
        result = await get_counter_value()
        print(f"Counter: {result}")

    await flow.shutdown()

if __name__ == '__main__':
    asyncio.run(main())
```

### Advanced Usage

#### Agent Chaining

```python
async def chain_agents():
    async with AcademyIntegration(flow) as integration:
        # Create a workflow from multiple agents
        workflow = integration.create_workflow_from_academy_chain([
            (Counter, 'get_count', (), {}),
            (AnotherAgent, 'process_value', (), {}),
        ])

        result = await workflow()
        return result
```

#### Custom Configuration

```python
counter_task = integration.create_agent_task(
    Counter,
    'get_count',
    agent_id='my_counter',
    agent_args=(),
    agent_kwargs={'initial_value': 10},
    startup_delay=0.5  # Wait for loops to start
)
```

## LangGraph Integration

Expose AsyncFlow tasks as LangChain tools with built-in fault tolerance.

### Basic Usage

```python
import asyncio
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine
from flowgentic.langgraph import LangGraphIntegration
from langgraph.graph import StateGraph

async def main():
    # Setup AsyncFlow
    backend = await ConcurrentExecutionBackend()
    flow = await WorkflowEngine.create(backend=backend)
    integration = LangGraphIntegration(flow)

    # Create tools from AsyncFlow tasks
    @integration.asyncflow_tool
    async def get_weather(city: str) -> str:
        # Your async task implementation
        return f"Weather in {city}: Sunny"

    @integration.asyncflow_tool
    async def get_news(topic: str) -> str:
        return f"Latest news on {topic}"

    # Use with LangGraph
    workflow = StateGraph(dict)
    # ... build your graph

    await flow.shutdown()

asyncio.run(main())
```

### Fault Tolerance

Built-in retry logic with exponential backoff:

```python
from flowgentic.langgraph import RetryConfig

@integration.asyncflow_tool(
    retry=RetryConfig(
        max_attempts=3,
        base_backoff_sec=0.5,
        max_backoff_sec=8.0,
        timeout_sec=30.0
    )
)
async def flaky_api_call(endpoint: str) -> dict:
    # Your API call here
    pass
```

### Existing Tasks

Convert existing AsyncFlow tasks to LangChain tools:

```python
@integration.asyncflow_tool
async def existing_task(data: str) -> str:
    return f"Processed: {data}"
```

## Examples

### Academy Integration Examples

- [Basic Actor Client](examples/academy-integration/01-actor-client/run.py)
- [Agent Loop](examples/academy-integration/02-agent-loop/run.py)

### LangGraph Integration Examples

- [Parallel Tools](examples/langgraph-integration/01-parallel-tools.py)
- [Parallel Agents](examples/langgraph-integration/02-parallel-agents.py)

## API Reference

### AcademyIntegration

#### `create_agent_task(agent_class, action_name, agent_id=None, agent_args=(), agent_kwargs=None, startup_delay=None)`

Create a workflow task from an Academy agent action.

**Parameters:**

- `agent_class`: Academy Agent class
- `action_name`: Name of the action method to call
- `agent_id`: Optional custom agent ID
- `agent_args`: Arguments for agent constructor
- `agent_kwargs`: Keyword arguments for agent constructor
- `startup_delay`: Time to wait after launching agent

**Returns:** Callable workflow task function

#### `create_workflow_from_academy_chain(agents_and_actions, workflow_name="academy_workflow")`

Create a complete workflow from a chain of Academy agents.

### LangGraphIntegration

#### `asyncflow_tool(func=None, retry=None)`

Decorator to register an async function as both AsyncFlow task and LangChain tool.

### RetryConfig

Configuration for retry/backoff behavior:

- `max_attempts`: Total attempts (default: 3)
- `base_backoff_sec`: Base delay for exponential backoff (default: 0.5)
- `max_backoff_sec`: Maximum backoff delay (default: 8.0)
- `timeout_sec`: Per-attempt timeout (default: 30.0)
- `raise_on_failure`: Whether to raise on final failure (default: True)

## Requirements

- Python 3.13+
- Academy-py
- radical-asyncflow
- langgraph (for LangGraph integration)

## License

MIT License - see LICENSE file for details.

