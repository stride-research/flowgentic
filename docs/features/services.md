# SERVICE_TASK

Persistent internal services that initialize once and maintain continual uptime.

- **One-time initialization**: Connect to databases, APIs, or external services once
- **Service persistence**: Service instances stay alive across multiple invocations
- **Continual uptime**: Maintain connections, state, and resources
- **Resource efficiency**: Eliminate repeated expensive connection setup
- **Direct callable**: Use as regular async functions (no @tool wrapper)

## Use Cases

- Database connection pools
- Redis/cache clients
- External API clients with authentication
- Background processing services
- Internal workflow services
- Any stateful service that should persist but doesn't need @tool wrapper

## Example
### Service Task
```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.SERVICE_TASK
)
async def database_query_service(sql: str) -> str:
    """
    Execute database queries using a persistent connection pool.
    The pool is created once and reused for all queries.
    """
    # Lazy initialization: create service on first call
    if not hasattr(database_query_service, '_service'):
        database_query_service._service = DatabaseService()
        print("🔌 Database service initialized!")
    
    # Use the persistent service instance
    result = await database_query_service._service.execute_query(sql)
    return f"Query result: {result}"

# Direct calls (no .ainvoke needed)
result1 = await database_query_service("SELECT * FROM users")
result2 = await database_query_service("SELECT * FROM products")
# Both queries use the same service instance!
```

### Service Task called as agent tool
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

1. **First invocation**: Service instance created 
2. **Service persists**: Instance stays alive in memory with continual uptime
3. **Subsequent calls**: Same service instance handles all requests

## Calling Convention

Since this is NOT wrapped as a `@tool`, call it directly like a normal async function:

**Direct usage:**
```python
result = await database_query_service("SELECT * FROM orders")
```

**In workflow nodes:**
```python
async def data_extraction_node(state: WorkflowState):
    # Call service directly
    data = await database_query_service(state.query)
    state.extracted_data = data
    return state
```

## Benefits

- **Continual uptime**: Service stays alive across all calls
- **No connection overhead**: Connections established once, reused forever
- **Resource efficiency**: Share expensive resources across all calls
- **Simple syntax**: Direct calls, no .ainvoke() needed

## Comparision between `SERVICE_TASK` and `AGENT_TOOL_AS_SERVICE`

**Use SERVICE_TASK when:**
- Service used in workflow nodes
- Manual/direct function calls
- Don't need @tool wrapper
- Simple async function interface

**Use AGENT_TOOL_AS_SERVICE when:**
- Service should be callable by agents
- Need @tool wrapper
- LLM decides when to invoke

## Examples

- [SERVICE_TASK example](https://github.com/stride-research/flowgentic/blob/main/examples/langgraph-integration/service-task/01-service-task-example.py)
- [AGENT_TOOL_AS_SERVICE example](https://github.com/stride-research/flowgentic/blob/main/examples/langgraph-integration/service-task/02-agent-tool-as-service-example.py)

## API
Refer to the execution wrappers API: [API Reference](../../api/flowgentic/langGraph/execution_wrappers/)
