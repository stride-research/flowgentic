# AGENT_TOOL_AS_SERVICE

Persistent services exposed as LLM-callable tools with continual uptime.

- **LLM integration**: Services callable by language models as tools with `@tool` wrapper
- **Persistent service instances**: Service initialized once, stays alive across multiple calls
- **Intelligent tool selection**: LLM decides when to use the service
- **Continual uptime**: Service maintains connections, sessions, and state
- **Automatic @tool wrapping**: Ready for use with LangChain agents

## Use Cases

- Weather APIs that agents can query
- Database search tools for retrieval
- External data enrichment services
- Real-time information lookup
- Any API that should be agent-accessible
- Services needing persistent HTTP sessions

## Example
```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.AGENT_TOOL_AS_SERVICE,
    tool_description="Get current weather for any city with persistent API session"
)
async def weather_api_tool(city: str) -> str:
    """
    Fetch weather data using a persistent API client.
    The service instance is created once and reused for all calls.
    """
    # Lazy initialization: create service on first call
    if not hasattr(weather_api_tool, '_client'):
        weather_api_tool._client = WeatherAPIClient()
        print("🌐 API client session created!")
    
    # Use the persistent service instance
    result = await weather_api_tool._client.fetch_weather(city)
    return f"Weather: {result['temperature']}°F, {result['conditions']}"

# Agent can call this tool (automatic @tool wrapper)
agent = create_react_agent(llm, tools=[weather_api_tool])

# Manual calls require .ainvoke() (LangChain tool convention)
result = await weather_api_tool.ainvoke({"city": "San Francisco"})
```

## How It Works

1. **First call**: Service instance created (lazy initialization)
2. **Service persists**: Instance stays alive in memory with continual uptime
3. **Subsequent calls**: Same service instance handles all requests
4. **State accumulates**: Service maintains connections, metrics, session data

## Calling Convention

Since this wraps the service as a LangChain `@tool`:

**Agent usage (automatic):**
```python
agent = create_react_agent(model, tools=[weather_api_tool])
await agent.ainvoke({"messages": [HumanMessage("Get weather for NYC")]})
```

**Manual usage (requires .ainvoke):**
```python
result = await weather_api_tool.ainvoke({"city": "New York"})
```

## Benefits

- **Continual uptime**: Service stays alive across all calls
- **Reduced latency**: No connection overhead on repeated calls
- **State maintenance**: Track metrics, maintain sessions, keep connections
- **Resource efficiency**: Share expensive resources (connections, auth) across calls
- **Agent-ready**: Automatic @tool wrapper for LangChain agents

## vs SERVICE_TASK

| Feature | AGENT_TOOL_AS_SERVICE | SERVICE_TASK |
|---------|----------------------|--------------|
| Wrapped as @tool | ✅ Yes | ❌ No |
| Service persistence | ✅ Yes | ✅ Yes |
| Manual call syntax | `.ainvoke(dict)` | Direct call |
| Best for | Agent tools | Nodes, direct calls |

## Examples

- [AGENT_TOOL_AS_SERVICE example](https://github.com/stride-research/flowgentic/blob/main/examples/langgraph-integration/service-task/02-agent-tool-as-service-example.py)

## API

Refer to the execution wrappers API: [API Reference](../../api/flowgentic/langGraph/execution_wrappers/)