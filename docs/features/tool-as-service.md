# TOOL_AS_SERVICE

Persistent services exposed as LLM-callable tools with automatic caching.

- **LLM integration**: Services callable by language models as tools
- **Persistent connections**: Maintain API connections across multiple LLM calls
- **Intelligent tool selection**: LLM decides when to use the service
- **Connection reuse**: Share expensive connections across tool invocations
- **Data caching**: Cache tool results to avoid redundant API calls

## Use Cases

- Weather APIs that LLMs can query
- Database search tools for retrieval
- External data enrichment services
- Real-time information lookup
- Any API that should be LLM-accessible

## Example
```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.TOOL_AS_SERVICE,
    tool_description="Get current weather for any city"
)
async def get_weather(city: str):
    """
    Fetch weather data for a given city.
    The API connection is established once and reused.
    """
    # Connection initialized once, cached for future calls
    # Results can also be cached per city
    return {"temperature": 72, "conditions": "sunny"}

# LLM can call this tool when needed
# Connection persists across all invocations
agent = create_react_agent(llm, tools=[get_weather])
```

## How It Works

1. **First LLM call**: Service initializes, connects to external API
2. **Service cached**: Connection persists in memory
3. **Subsequent calls**: LLM uses cached service connection
4. **Optional result caching**: Cache API responses to avoid redundant calls

## Benefits

- **Reduced latency**: No connection overhead on repeated calls
- **Cost efficiency**: Fewer API authentication requests
- **Better UX**: Faster responses to user queries
- **Resource optimization**: Share connections across LLM interactions

## Examples

- [TOOL_AS_SERVICE with caching](https://github.com/stride-research/flowgentic/blob/main/examples/langgraph-integration/07-tool-as-service-example.py)

## API

Refer to the execution wrappers API: [API Reference](../../api/flowgentic/langGraph/execution_wrappers/)