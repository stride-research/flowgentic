# AGENT_TOOL_AS_MCP
Integration for **real MCP (Model Context Protocol)** servers, allowing Flowgentic agents to dynamically connect to external tool providers such as SQLite MCP servers, data APIs, or Anthropic’s MCP ecosystem.

- **Dynamic tool discovery**: Automatically fetches and registers each MCP tool as an async, LLM-callable function  
- **LLM-callable**: All discovered MCP tools are exposed to the agent for autonomous use  
- **Async orchestration**: Fully integrated into `AsyncFlow` for non-blocking execution  
- **Fault tolerance**: Includes retry, timeout, and fallback mechanisms  
- **Configurable execution**: Can run locally (default) or be extended for remote orchestration in HPC environments  

---

## Use Cases

- Connect to external MCP servers (e.g., SQLite, Anthropic, or custom implementations)  
- Dynamically expand available tools at runtime  
- Integrate structured data operations with natural language agents  
- Maintain full async orchestration under Flowgentic  

---

## Example

```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.AGENT_TOOL_AS_MCP,
    tool_description="Expose SQLite MCP tools as callable agents"
)
async def database_specialist(query: str):
    '''
    Connects to a SQLite MCP server, discovers tools such as:
    - sqlite_execute
    - sqlite_get_catalog
    - sqlite_describe_table
    and registers them as async Flowgentic tools callable by the LLM.
    '''
    pass  # The asyncflow wrapper handles registration and execution
```

**Agent creation:**
```python
agent = create_react_agent(
    llm,
    tools=[database_specialist]  # All discovered MCP tools are accessible
)
```

---

## How It Works

1. **Connection established:** The MCP client connects to the configured server (e.g., SQLite MCP).  
2. **Tool discovery:** The wrapper fetches all available tools dynamically via the MCP SDK.  
3. **Async wrapping:** Each tool is registered as an `AsyncFlow`-managed task with retry and timeout logic.  
4. **Agent orchestration:** The LLM can call any MCP-discovered tool directly in context.  
5. **Result handling:** Tool outputs are streamed back into the reasoning flow.  

---

## Configuration

Default configuration uses a **local MCP server** with stdio transport:
```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.AGENT_TOOL_AS_MCP,
    mcp_server_script="python",
    mcp_server_args=["-m", "mcp.server.sqlite"]
)
```

Optional parameters allow future remote deployment:
```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.AGENT_TOOL_AS_MCP,
    local_execution=False,  # Future remote orchestration support
    mcp_server_uri="https://mcp.yourorg.net"
)
```

---

## Infrastructure Benefits

- **Truly async:** Uses Radical AsyncFlow for concurrent execution  
- **Resilient:** Retry, timeout, and exception handling built-in  
- **Observable:** Integrated with Flowgentic telemetry for tracing and debugging  
- **Composable:** Works seamlessly within other `AsyncFlowType` nodes  

---

## Examples

- [MCP Sequential Example](https://github.com/stride-research/flowgentic/blob/main/examples/langgraph-integration/mcp/sequential/sales_analytics/main.py)  
- [MCP Toy Demo](https://github.com/stride-research/flowgentic/blob/main/examples/langgraph-integration/mcp/toy/toy-mcp-demo.py)

---

## API

See [Flowgentic Execution Wrappers API](../../api/flowgentic/langGraph/execution_wrappers/) for details.  