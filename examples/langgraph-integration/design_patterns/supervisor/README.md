# Supervisor Pattern with Radical AsyncFlow

Demonstrates the **official LangGraph supervisor pattern** where a supervisor ReAct agent dynamically delegates tasks to specialized worker agents using handoff tools, while all execution happens through radical asyncflow for HPC compatibility.

This follows the standard LangGraph supervisor architecture, making it completely **use-case agnostic** and extensible.

## What this example shows

- **Supervisor ReAct Agent**: LLM-powered supervisor with handoff tools (official LangGraph pattern)
- **Handoff Tools**: Tools that return `Command` objects with `Send` for dynamic routing
- **AsyncFlow Execution**: All tools and worker nodes execute via radical asyncflow (HPC-ready)
- **Multi-Turn Workflows**: Workers return to supervisor, enabling complex delegation chains
- **Use-Case Agnostic**: Add new agents by creating handoff tools—no workflow logic changes needed

## How it works

1. **User** sends a query to the **Supervisor**
2. **Supervisor ReAct Agent** analyzes the request using LLM reasoning
3. **Supervisor** calls a handoff tool (e.g., `transfer_to_researcher`) with task description
4. **Handoff Tool** (via asyncflow) returns `Command(goto=[Send(agent_name, task)])` 
5. **Worker Agent** executes with specialized tools (via asyncflow)
6. **Worker** returns to **Supervisor**, which can delegate to another agent or finish

This creates a **fully dynamic, LLM-driven workflow** where the supervisor intelligently routes work based on the request, not hardcoded rules.

## Architecture

```
User Query
    ↓
Supervisor ReAct Agent (with handoff tools: transfer_to_researcher, transfer_to_calculator)
    ├─→ Researcher (web_search tool) ──→ Returns to Supervisor
    └─→ Calculator (calculate tool) ──→ Returns to Supervisor
         ↓
    Supervisor can delegate to another agent or finish
```

**Multi-turn example:**
1. User: "Research the population of Tokyo and calculate 15% of it"
2. Supervisor → Researcher (gets population)
3. Researcher → Supervisor (with result)
4. Supervisor → Calculator (calculates 15%)
5. Calculator → Supervisor (with final answer)

## Key Components

### Handoff Tools (Official LangGraph Pattern + AsyncFlow)

```python
def create_handoff_tool(agent_name: str, description: str):
    @agents_manager.agents.asyncflow(flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION)
    async def handoff_tool(task_description: str) -> Command:
        """Transfer work to specialized agent."""
        task_message = HumanMessage(content=task_description)
        return Command(goto=[Send(agent_name, {"messages": [task_message]})],)
    
    handoff_tool.__name__ = f"transfer_to_{agent_name}"
    handoff_tool.__doc__ = description
    return handoff_tool

transfer_to_researcher = create_handoff_tool("researcher", "For research tasks...")
```

**Critical**: Handoff tools execute via asyncflow but return `Command` objects for LangGraph routing.

### Supervisor ReAct Agent

```python
supervisor_agent = create_react_agent(
    model=ChatLLMProvider(...),
    tools=[transfer_to_researcher, transfer_to_calculator],
    prompt="You are a supervisor managing agents. Delegate work using transfer tools..."
)
```

The supervisor **uses LLM reasoning** to decide which agent to call and what task description to provide.

### Worker Tools (AsyncFlow)

```python
@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION)
async def web_search(query: str) -> str:
    """Domain-specific tool executed via asyncflow."""
    return "Search results..."
```

### Worker Nodes (AsyncFlow Execution Block)

```python
@agents_manager.agents.asyncflow(flow_type=AsyncFlowType.EXECUTION_BLOCK)
async def researcher_node(state: MessagesState) -> Command:
    """Worker agent wrapped for asyncflow execution."""
    result = await researcher_agent.ainvoke(state)
    return Command(update={"messages": result["messages"]}, goto="supervisor")
```

## How to run

```bash
# Demo mode (without API key) - shows architecture using mock LLMs:
make examples-supervisor

# Full mode (with API key) - actual LLM-driven routing and tool calls:
export OPEN_ROUTER_API_KEY="your-key-here"
make examples-supervisor
```

### Demo Mode vs Full Mode

**Demo Mode** (no API key):
- Uses mock LLMs to demonstrate the architecture
- Supervisor generates text about routing but doesn't actually call handoff tools
- Shows proper asyncflow registration and graph structure
- Generates introspection report showing the supervisor pattern architecture
- Perfect for understanding the pattern without API costs

**Full Mode** (with API key):
- Supervisor uses actual LLM reasoning to analyze requests
- Dynamically calls handoff tools based on request content
- Worker agents execute with real LLM + tool interactions via asyncflow
- Complete multi-turn workflows with actual delegation
- Demonstrates production-ready use-case agnostic routing

**Expected output (Demo Mode)**:
```
👔 Supervisor: Let me transfer this to the researcher agent...
✅ Workflow completed successfully!
📊 Introspection report generated
📈 Graph visualization rendered
```

**Expected output (Full Mode)**:
```
👔 Supervisor: Analyzing request...
📤 Supervisor → Researcher: What is the capital of France?...
🔍 Researcher Agent: Starting research...
🔍 Researcher: Using web_search tool...
✅ Researcher Agent: Task complete
👔 Supervisor: Based on research, the answer is Paris.
```

## Differences from Standard LangGraph

**Standard LangGraph** supervisor uses `@tool` decorator directly and executes in LangGraph's runtime.

**This implementation** uses `@asyncflow` decorator so everything runs through radical asyncflow's HPC-capable execution backend, while LangGraph provides orchestration and routing.

## Files

- `main.py` — Complete supervisor example with worker agents
- `README.md` — This file

## Extending (Use-Case Agnostic!)

To add a new specialized agent, simply:

1. **Create worker tools** (domain-specific):
   ```python
   @asyncflow(flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION)
   async def my_tool(param: str) -> str:
       # Tool logic
   ```

2. **Create handoff tool**:
   ```python
   transfer_to_my_agent = create_handoff_tool(
       "my_agent", 
       "Description of when to use this agent"
   )
   ```

3. **Create worker node**:
   ```python
   @asyncflow(flow_type=AsyncFlowType.EXECUTION_BLOCK)
   async def my_agent_node(state: MessagesState) -> MessagesState:
       agent = create_react_agent(model=..., tools=[my_tool])
       return {"messages": (await agent.ainvoke(state))["messages"]}
   ```

4. **Update supervisor**:
   - Add `transfer_to_my_agent` to supervisor's tools list
   - Add `"my_agent"` to `destinations` tuple
   - Add node: `workflow.add_node("my_agent", my_agent_node)`
   - Add edge: `workflow.add_edge("my_agent", "supervisor")`

**No workflow logic changes needed!** The supervisor's LLM reasoning handles when to route to the new agent based on the handoff tool's description.

