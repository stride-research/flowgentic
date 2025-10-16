# Building a Chatbot with FlowGentic

This tutorial walks you through building an interactive chatbot that uses tools, memory, and HPC execution with FlowGentic's LangGraph integration.

## What You'll Build

By the end of this tutorial, you'll have a chatbot that:

- **Executes tools in parallel** on HPC backends (weather and traffic extraction)
- **Maintains conversation memory** across interactions using checkpointing
- **Structures responses** using Pydantic schemas for type safety
- **Runs deterministic tasks** on distributed infrastructure
- **Logs conversations** for debugging and analysis

## Prerequisites

- Python 3.9+
- FlowGentic installed: `pip install flowgentic`
- An OpenRouter API key (or another LLM provider)

Set your environment variable:

```bash
export OPEN_ROUTER_API_KEY=sk-or-...
```

Or use a `.env` file:

```bash
# .env
OPEN_ROUTER_API_KEY=sk-or-...
```

## Step 1: Import Dependencies

Start by importing the necessary modules from FlowGentic, LangGraph, and LangChain.

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
import random
from typing import Annotated
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.agents import AsyncFlowType, BaseLLMAgentState
from flowgentic.utils.llm_providers import ChatLLMProvider
from dotenv import load_dotenv

load_dotenv()
```

**Key imports:**
- `LangraphIntegration`: Main entry point for FlowGentic's LangGraph integration
- `AsyncFlowType`: Enum defining execution types (TOOL, NODE, AGENT)
- `BaseLLMAgentState`: Base class for typed state management
- `ChatLLMProvider`: Unified interface for LLM providers

## Step 2: Define Your State Schema

Create a custom state class that extends `BaseLLMAgentState`. This defines what data flows through your agent graph.

```python
class WorkflowState(BaseLLMAgentState):
    pass  # Inherits 'messages' field from BaseLLMAgentState
```

`BaseLLMAgentState` provides:
- `messages`: List of conversation messages (required for LangGraph message passing)
- Automatic state merging and reducer configuration

For custom fields, extend it:

```python
class WorkflowState(BaseLLMAgentState):
    custom_field: str = ""
    status: str = "pending"
```

## Step 3: Define Response Schema

Use Pydantic to define structured outputs from your agent. This ensures type safety and validation.

```python
class DayVerdict(BaseModel):
    looks_like_a_good_day: bool
    reason: str
```

**Why structured responses?**
- Type-safe outputs for downstream processing
- Automatic validation of LLM responses
- Clear contract between agent and application logic

## Step 4: Initialize the HPC Backend

Set up the RADICAL AsyncFlow backend that will execute your agent tasks on HPC infrastructure.

```python
async def start_app():
    # Create a ThreadPoolExecutor backend for concurrent execution
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())
    
    async with LangraphIntegration(backend=backend) as agents_manager:
        # Your agent code goes here
        pass
```

**API Reference:**
- `ConcurrentExecutionBackend`: Wraps execution backends (ThreadPoolExecutor, ProcessPoolExecutor)
- `LangraphIntegration`: Context manager that manages agent lifecycle and HPC integration
- `agents_manager`: Provides access to `.agents`, `.utils`, and `.agent_logger`

## Step 5: Configure the LLM Provider

Initialize your LLM provider. FlowGentic supports multiple providers through a unified interface.

```python
llm = ChatLLMProvider(
    provider="OpenRouter", 
    model="google/gemini-2.5-flash"
)
```

**Supported providers:**
- `OpenRouter`: Access to multiple models (Google, Anthropic, etc.)
- `OpenAI`: Direct OpenAI API integration
- `Anthropic`: Claude models

**Common models:**
- `google/gemini-2.5-flash`: Fast, cost-effective
- `anthropic/claude-3.5-sonnet`: High-quality reasoning
- `openai/gpt-4-turbo`: OpenAI's latest

## Step 6: Define HPC-Backed Tools

Create tools that will execute on your HPC backend using the `@asyncflow` decorator.

```python
@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.TOOL)
async def weather_extractor(city: str):
    """Extracts the weather for any given city"""
    return {
        "temperature_celsius": 25,
        "humidity_percentage": 40,
    }

@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.TOOL)
async def traffic_extractor(city: str):
    """Extracts the amount of traffic for any given city"""
    return {
        "traffic_percentage": 90
    }
```

**API Reference:**
- `@agents_manager.agents.asyncflow()`: Decorator that registers functions for HPC execution
- `flow_type=AsyncFlowType.TOOL`: Marks function as a LangChain tool (callable by LLM)
- **Docstrings are important**: The LLM uses them to understand when to call tools

**AsyncFlowType options:**
- `TOOL`: LangChain-compatible tool (can be called by LLM)
- `NODE`: LangGraph node (part of graph flow)
- `AGENT`: Full agent with tool-calling capabilities

## Step 7: Bind Tools to LLM

Give your LLM access to the tools you've defined.

```python
tools = [weather_extractor, traffic_extractor]
llm_with_tools = llm.bind_tools(tools)
```

This enables the LLM to:
1. Recognize when tools should be called
2. Generate proper tool invocation parameters
3. Process tool results in subsequent reasoning

## Step 8: Create the LLM Invocation Node

Define a node that invokes the LLM with the current conversation state.

```python
async def invoke_llm(state: WorkflowState):
    response = await llm_with_tools.ainvoke(state.messages)
    return WorkflowState(messages=[response])
```

**What happens here:**
1. Takes current state (with message history)
2. Sends to LLM with tool access
3. Returns updated state with LLM response
4. LLM may include tool calls in its response

## Step 9: Add a Deterministic HPC Task

Create a task that runs deterministic operations on HPC infrastructure.

```python
@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.NODE)
async def deterministic_task_internal(state: WorkflowState):
    file_path = "im-working.txt"
    with open(file_path, "w") as f:
        f.write("Hello world!")
    return {"status": "file_written", "path": file_path}
```

**Use cases for deterministic nodes:**
- File I/O operations
- Data preprocessing
- Computational tasks
- External API calls

## Step 10: Build the Agent Graph

Construct the LangGraph workflow by defining nodes and edges.

```python
workflow = StateGraph(WorkflowState)

# Add nodes
workflow.add_node("chatbot", invoke_llm)
workflow.add_node(
    "response_synthetizer",
    agents_manager.utils.structured_final_response(
        llm=llm, 
        response_schema=DayVerdict, 
        graph_state_schema=WorkflowState
    )
)
workflow.add_node("deterministic_task", deterministic_task_internal)
workflow.add_node("tools", ToolNode(tools))

# Set entry point
workflow.set_entry_point("deterministic_task")
```

**Node types:**
- `chatbot`: Main LLM invocation
- `response_synthetizer`: Structured output generator (uses Pydantic schema)
- `deterministic_task`: HPC task node
- `tools`: Tool execution node (handles LLM tool calls)

**API Reference:**
- `agents_manager.utils.structured_final_response()`: Helper that forces structured output
  - `llm`: LLM instance to use
  - `response_schema`: Pydantic model for output
  - `graph_state_schema`: State schema for proper typing

## Step 11: Define Graph Edges

Connect nodes to create your workflow logic.

```python
# Conditional edge: chatbot decides if tools are needed
workflow.add_conditional_edges(
    "chatbot",
    agents_manager.utils.needs_tool_invokation,
    {"true": "tools", "false": "response_synthetizer"}
)

# After tools, go back to chatbot
workflow.add_edge("tools", "chatbot")

# From deterministic task
workflow.add_edge("deterministic_task", "chatbot")
workflow.add_edge("deterministic_task", END)
```

**Edge types:**
- `add_edge(from, to)`: Direct connection
- `add_conditional_edges(from, condition, mapping)`: Branch based on condition

**Built-in conditions:**
- `agents_manager.utils.needs_tool_invokation`: Returns "true" if LLM requested tool calls

**Workflow:**
```
deterministic_task → chatbot → [needs tools?]
                                ├─ true → tools → chatbot
                                └─ false → response_synthetizer → END
```

## Step 12: Add Memory with Checkpointing

Enable conversation memory using LangGraph's checkpointing system.

```python
checkpointer = InMemorySaver()
app = workflow.compile(checkpointer=checkpointer)

thread_id = random.randint(0, 10)
config = {"configurable": {"thread_id": thread_id}}
```

**Checkpointing features:**
- Saves state after each node execution
- Enables conversation memory across interactions
- Supports rollback and time-travel debugging
- Thread-based isolation (different conversations use different thread_ids)

**Other checkpoint backends:**
- `SqliteSaver`: Persistent storage
- Custom backends: Implement `BaseCheckpointSaver`

## Step 13: Visualize the Graph

FlowGentic provides utilities to render your graph structure and generate execution artifacts.

```python
# Simple graph visualization only
await agents_manager.utils.render_graph(app)

# Or generate full execution artifacts after running the workflow
await agents_manager.generate_execution_artifacts(
    app, __file__, final_state=last_state
)
```

**What `generate_execution_artifacts()` provides:**
- Creates the `agent_execution_results/` directory
- Generates an execution summary markdown report with timing and state
- Renders a visual graph of your workflow

This generates a visual representation of your workflow, showing:
- All nodes and their connections
- Conditional branches
- Entry and exit points

## Step 14: Implement the Interaction Loop

Create an interactive chat loop that streams responses.

```python
while True:
    user_input = input("User: ").lower()
    
    if user_input in ["quit", "q", "-q", "exit"]:
        print("Goodbye!")
        last_state = app.get_state(config)
        print(f"Last state: {last_state}")
        
        # Log the conversation
        agents_manager.agent_logger.flush_agent_conversation(
            conversation_history=last_state.values.get("messages", [])
        )
        return
    
    # Create state with user message
    current_state = WorkflowState(messages=[HumanMessage(content=user_input)])
    
    # Stream execution
    async for chunk in app.astream(
        current_state, 
        stream_mode="values", 
        config=config
    ):
        if chunk["messages"]:
            last_msg = chunk["messages"][-1]
            if isinstance(last_msg, AIMessage):
                if hasattr(last_msg, "content") and last_msg.content:
                    print(f"Assistant: {last_msg.content}")
                if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                    print(f"Tool calls: {last_msg.tool_calls}")
        print(chunk)
        print("=" * 30)
```

**Streaming benefits:**
- Real-time updates as nodes execute
- Better user experience (progressive output)
- Debug visibility into graph execution

**Stream modes:**
- `"values"`: Stream complete state after each node
- `"updates"`: Stream only state changes
- `"messages"`: Stream individual messages

## Step 15: Run the Application

Execute your chatbot application.

```python
if __name__ == "__main__":
    asyncio.run(start_app())
```

## Example Interaction

```
User: What's the weather and traffic like in San Francisco?
Tool calls: [weather_extractor(city='San Francisco'), traffic_extractor(city='San Francisco')]
Assistant: Based on the data, San Francisco has pleasant weather at 25°C with 40% humidity, 
but traffic is quite heavy at 90%. It looks like a good day weather-wise, but you might 
want to avoid rush hour!
```

---

## Complete Code

<details>
<summary>Click to expand full code</summary>

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
import random
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel
from radical.asyncflow import ConcurrentExecutionBackend

from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.agents import AsyncFlowType, BaseLLMAgentState
from flowgentic.utils.llm_providers import ChatLLMProvider
from dotenv import load_dotenv

load_dotenv()


class WorkflowState(BaseLLMAgentState):
    pass


class DayVerdict(BaseModel):
    looks_like_a_good_day: bool
    reason: str


async def start_app():
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

    async with LangraphIntegration(backend=backend) as agents_manager:
        llm = ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash")

        @agents_manager.agents.asyncflow(flow_type=AsyncFlowType.TOOL)
        async def weather_extractor(city: str):
            """Extracts the weather for any given city"""
            return {
                "temperature_celsius": 25,
                "humidity_percentage": 40,
            }

        @agents_manager.agents.asyncflow(flow_type=AsyncFlowType.TOOL)
        async def traffic_extractor(city: str):
            """Extracts the amount of traffic for any given city"""
            return {"traffic_percentage": 90}

        @agents_manager.agents.asyncflow(flow_type=AsyncFlowType.NODE)
        async def deterministic_task_internal(state: WorkflowState):
            file_path = "im-working.txt"
            with open(file_path, "w") as f:
                f.write("Hello world!")
            return {"status": "file_written", "path": file_path}

        tools = [weather_extractor, traffic_extractor]
        llm_with_tools = llm.bind_tools(tools)

        async def invoke_llm(state: WorkflowState):
            response = await llm_with_tools.ainvoke(state.messages)
            return WorkflowState(messages=[response])

        workflow = StateGraph(WorkflowState)

        workflow.add_node("chatbot", invoke_llm)
        workflow.add_node(
            "response_synthetizer",
            agents_manager.utils.structured_final_response(
                llm=llm, response_schema=DayVerdict, graph_state_schema=WorkflowState
            ),
        )
        workflow.add_node("deterministic_task", deterministic_task_internal)
        workflow.add_node("tools", ToolNode(tools))

        workflow.set_entry_point("deterministic_task")

        workflow.add_conditional_edges(
            "chatbot",
            agents_manager.utils.needs_tool_invokation,
            {"true": "tools", "false": "response_synthetizer"},
        )
        workflow.add_edge("tools", "chatbot")
        workflow.add_edge("deterministic_task", "chatbot")
        workflow.add_edge("deterministic_task", END)

        checkpointer = InMemorySaver()
        app = workflow.compile(checkpointer=checkpointer)
        thread_id = random.randint(0, 10)
        config = {"configurable": {"thread_id": thread_id}}

        # Optional: Render graph before starting interaction
        await agents_manager.utils.render_graph(app)

        while True:
            user_input = input("User: ").lower()
            if user_input in ["quit", "q", "-q", "exit"]:
                print("Goodbye!")
                last_state = app.get_state(config)
                print(f"Last state: {last_state}")
                agents_manager.agent_logger.flush_agent_conversation(
                    conversation_history=last_state.values.get("messages", [])
                )
                return

            current_state = WorkflowState(messages=[HumanMessage(content=user_input)])

            async for chunk in app.astream(
                current_state, stream_mode="values", config=config
            ):
                if chunk["messages"]:
                    last_msg = chunk["messages"][-1]
                    if isinstance(last_msg, AIMessage):
                        if hasattr(last_msg, "content") and last_msg.content:
                            print(f"Assistant: {last_msg.content}")
                        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                            print(f"Tool calls: {last_msg.tool_calls}")
                print(chunk)
                print("=" * 30)


if __name__ == "__main__":
    asyncio.run(start_app())
```

</details>

---

## Troubleshooting

### LLM not calling tools
- Check that tool docstrings are descriptive
- Verify `llm.bind_tools(tools)` was called
- Ensure tools are added to `ToolNode(tools)`

### Memory not persisting
- Verify `checkpointer` is passed to `workflow.compile()`
- Check that `thread_id` remains consistent across interactions
- Use `app.get_state(config)` to inspect saved state

### HPC execution failures
- Ensure `backend` is properly initialized with `await`
- Check that `@asyncflow` decorator is used correctly
- Verify async context manager (`async with LangraphIntegration`)

For detailed API documentation, see the [API Reference](../../api/reference.md).

Explore other design patterns:
- [Sequential/Pipeline Pattern](sequential.md) — Interactive conversational agents
- [Supervisor Pattern](supervisor.md) — Supervisor agent coordination
- [Hierarchical Agent Pattern](hierachical.md) — Advanced supervisor design pattern
