# AGENT_TOOL_AS_MCP

Real MCP (Model Context Protocol) client integration for connecting to external MCP servers.

- **Real MCP integration**: Connect to external MCP servers via official MCP SDK
- **LLM-callable**: Expose MCP tools as LLM-callable functions
- **Async execution**: Non-blocking MCP server communication
- **Fault tolerance**: Built-in retry and fallback mechanisms
- **Production-ready**: Infrastructure proven for real MCP servers

## Use Cases

- Connect to Anthropic's MCP servers
- Integrate specialized reasoning models
- Delegate complex tasks to external compute
- Access external tool ecosystems
- Extend LLM capabilities with external services

## Example
```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.AGENT_TOOL_AS_MCP,
    tool_description="Call external MCP model for complex reasoning"
)
async def call_mcp_model(prompt: str):
    """
    Connects to MCP server and executes available tools.
    Currently connects to Anthropic's demo server with 10+ tools.
    """
    pass  # Implementation handled by wrapper

# LLM can intelligently decide when to use MCP
agent = create_react_agent(llm, tools=[call_mcp_model])
```

## How It Works

1. **LLM triggers call**: Language model decides MCP is needed
2. **Connect to MCP server**: Establish connection via NPX or configured server
3. **Execute MCP tool**: Call appropriate tool on MCP server (e.g., echo, add)
4. **Return results**: Send results back to LLM for reasoning
5. **Fallback on error**: Use placeholder if connection fails

## Configuration

Currently connects to Anthropic's demo MCP server by default. Production usage:
```python
# Configure custom MCP server
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.AGENT_TOOL_AS_MCP,
    mcp_server_script="npx",
    mcp_server_args=["-y", "@your-org/mcp-server"]
)
```

## Infrastructure Benefits

- **Proven async execution**: Runs through AsyncFlow with full retry/fault tolerance
- **Non-blocking**: Doesn't block other agent operations
- **Scalable**: Ready for production MCP deployments
- **Observable**: Full telemetry and introspection

## Examples

- [MCP real client demo](https://github.com/stride-research/flowgentic/blob/main/examples/langgraph-integration/08-mcp-placeholder-example.py)

## API

Refer to the execution wrappers API: [API Reference](../../api/flowgentic/langGraph/execution_wrappers/)