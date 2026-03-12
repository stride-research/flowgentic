# Building an Agent with Flowgentic

This tutorial walks you through building a LangGraph agent that offloads tool calls to an HPC backend using Flowgentic's two decorators: `@hpc_task` and `@hpc_block`.

## What You'll Build

A weather + traffic chatbot that:

- **Executes tool calls in parallel** on the HPC backend
- **Runs graph nodes** as HPC blocks
- Uses the standard LangGraph graph API — no rewrites needed

## Prerequisites

- Python 3.10+
- Flowgentic with LangGraph + AsyncFlow extras:
  ```bash
  pip install "flowgentic[langgraph,asyncflow] @ git+https://github.com/stride-research/flowgentic.git@main"
  ```
- An LLM API key (OpenRouter, OpenAI, etc.)

## Step 1: Import Dependencies

```python
import asyncio
from concurrent.futures import ProcessPoolExecutor
from typing import Annotated

from langgraph.graph import StateGraph, add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel
from radical.asyncflow import LocalExecutionBackend, WorkflowEngine

from flowgentic.agent_orchestration_frameworks.langgraph import LanGraphOrchestrator
from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine
from dotenv import load_dotenv

load_dotenv()
```

**Key imports:**

- `LanGraphOrchestrator` — provides the `@hpc_task` and `@hpc_block` decorators
- `AsyncFlowEngine` — Flowgentic wrapper around RADICAL AsyncFlow
- `LocalExecutionBackend` — runs tasks locally (swap for a cluster executor on HPC)

## Step 2: Define Your State Schema

```python
class WorkflowState(BaseModel):
    messages: Annotated[list, add_messages]
```

Standard LangGraph state — no Flowgentic-specific base class required.

## Step 3: Boot the HPC Backend

```python
async def main():
    backend = await LocalExecutionBackend(ProcessPoolExecutor(max_workers=4))
    flow    = await WorkflowEngine.create(backend)

    engine       = AsyncFlowEngine(flow)
    orchestrator = LanGraphOrchestrator(engine)
```

`LocalExecutionBackend` with `ProcessPoolExecutor` runs tasks as subprocesses on your machine. On an HPC cluster replace it with the appropriate executor (e.g. RADICAL Pilot).

## Step 4: Decorate Tools with `@hpc_task`

```python
    @orchestrator.hpc_task
    async def fetch_weather(city: str = "SFO"):
        """Returns current weather for a city."""
        await asyncio.sleep(2)          # simulate HPC work
        return {"temperature": 22, "humidity": 55, "city": city}

    @orchestrator.hpc_task
    async def fetch_traffic(city: str = "SFO"):
        """Returns traffic congestion level for a city."""
        await asyncio.sleep(2)
        return {"traffic_pct": 75, "city": city}
```

- The decorator wraps the function as a **LangChain tool** so it can be passed to `ToolNode` and `llm.bind_tools()`.
- When the LLM triggers both tools, they run **concurrently** across available backend slots.
- **Docstrings matter** — the LLM reads them to decide when to call each tool.

## Step 5: Bind Tools to Your LLM

```python
    tools = [fetch_weather, fetch_traffic]
    llm   = your_llm.bind_tools(tools)
```

Any LangChain-compatible LLM works here.

## Step 6: Decorate a Graph Node with `@hpc_block`

```python
    @orchestrator.hpc_block
    async def agent_node(state: WorkflowState):
        response = await llm.ainvoke(state.messages)
        return {"messages": [response]}
```

The `@hpc_block` decorator schedules the node as an HPC block and emits lifecycle events (`block_wrap_start/end`). The returned callable is a plain async function — use it with `workflow.add_node()` as normal.

## Step 7: Build the LangGraph Graph

```python
    def should_continue(state: WorkflowState):
        last = state.messages[-1]
        return "tools" if getattr(last, "tool_calls", None) else "end"

    workflow = StateGraph(WorkflowState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", ToolNode(tools))

    workflow.set_entry_point("agent")
    workflow.add_conditional_edges(
        "agent", should_continue,
        {"tools": "tools", "end": "__end__"}
    )
    workflow.add_edge("tools", "agent")
    workflow.set_finish_point("agent")

    app = workflow.compile()
```

Nothing Flowgentic-specific here — the graph is pure LangGraph.

## Step 8: Run and Tear Down

```python
    result = await app.ainvoke(
        {"messages": [("user", "What's the weather and traffic in SFO?")]}
    )
    print(result)

    await flow.shutdown()


asyncio.run(main())
```

Always call `flow.shutdown()` to cleanly stop the backend workers.

---

## Complete Code

<details>
<summary>Click to expand</summary>

```python
import asyncio
from concurrent.futures import ProcessPoolExecutor
from typing import Annotated

from langgraph.graph import StateGraph, add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel
from radical.asyncflow import LocalExecutionBackend, WorkflowEngine

from flowgentic.agent_orchestration_frameworks.langgraph import LanGraphOrchestrator
from flowgentic.backend_engines.radical_asyncflow import AsyncFlowEngine
from dotenv import load_dotenv

load_dotenv()


class WorkflowState(BaseModel):
    messages: Annotated[list, add_messages]


async def main():
    backend = await LocalExecutionBackend(ProcessPoolExecutor(max_workers=4))
    flow    = await WorkflowEngine.create(backend)

    engine       = AsyncFlowEngine(flow)
    orchestrator = LanGraphOrchestrator(engine)

    @orchestrator.hpc_task
    async def fetch_weather(city: str = "SFO"):
        """Returns current weather for a city."""
        await asyncio.sleep(2)
        return {"temperature": 22, "humidity": 55, "city": city}

    @orchestrator.hpc_task
    async def fetch_traffic(city: str = "SFO"):
        """Returns traffic congestion level for a city."""
        await asyncio.sleep(2)
        return {"traffic_pct": 75, "city": city}

    tools = [fetch_weather, fetch_traffic]
    llm   = your_llm.bind_tools(tools)

    @orchestrator.hpc_block
    async def agent_node(state: WorkflowState):
        response = await llm.ainvoke(state.messages)
        return {"messages": [response]}

    def should_continue(state: WorkflowState):
        last = state.messages[-1]
        return "tools" if getattr(last, "tool_calls", None) else "end"

    workflow = StateGraph(WorkflowState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", ToolNode(tools))
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges(
        "agent", should_continue,
        {"tools": "tools", "end": "__end__"}
    )
    workflow.add_edge("tools", "agent")
    workflow.set_finish_point("agent")

    app    = workflow.compile()
    result = await app.ainvoke(
        {"messages": [("user", "What's the weather and traffic in SFO?")]}
    )
    print(result)
    await flow.shutdown()


asyncio.run(main())
```

</details>

---

## Antipatterns

### ❌ Forgetting `await flow.shutdown()`

Backend workers keep running until explicitly stopped. Always call `await flow.shutdown()` — ideally in a `try/finally` block.

```python
# ✅ GOOD
try:
    result = await app.ainvoke(...)
finally:
    await flow.shutdown()
```

### ❌ Defining tools outside the async context

`@orchestrator.hpc_task` calls `engine.emit()` at decoration time. Decorating tools before the engine is initialised will fail.

```python
# ❌ BAD — engine not yet created
@orchestrator.hpc_task
async def my_tool(): ...

# ✅ GOOD — decorate inside the async function after engine setup
async def main():
    engine       = AsyncFlowEngine(flow)
    orchestrator = LanGraphOrchestrator(engine)

    @orchestrator.hpc_task
    async def my_tool(): ...
```

### ❌ Using `ThreadPoolExecutor` instead of `ProcessPoolExecutor`

AsyncFlow serialises tasks with `cloudpickle` and ships them to workers. `ThreadPoolExecutor` shares memory space and can cause subtle race conditions. Use `ProcessPoolExecutor` (or a cluster executor) for isolation.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ImportError: langchain-core is required` | Install with `pip install "flowgentic[langgraph]"` |
| `TypeError: got unexpected keyword argument 'invocation_id'` | Your tool function has a `**kwargs` catch-all; remove it or check the engine version |
| Tools never execute, throughput = 0 | `n_of_tool_calls_per_agent` resolved to 0 in weak-scaling config; check integer division |
| Phantom ticks on x-axis plots (`3×10⁰`) | Use `FixedLocator`/`FixedFormatter` after `set_xscale("log")` |

---

Explore other design patterns:

- [Sequential / Pipeline Pattern](sequential.md)
- [Supervisor Pattern](supervisor.md)
- [Hierarchical Agent Pattern](hierachical.md)
