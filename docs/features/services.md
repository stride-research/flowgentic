# SERVICE_TASK

Persistent internal services that initialize once and maintain continual uptime across multiple invocations.

- **One-time initialization**: Connect to databases, APIs, or external services once
- **Service persistence**: Service instances stay alive across multiple invocations
- **Continual uptime**: Maintain connections, state, and resources without cold-starts
- **Resource efficiency**: Eliminate repeated expensive connection setup
- **Future control**: Returns `(result, future)` tuple for lifecycle management

## Overview

The `SERVICE_TASK` flow type is designed for production scenarios where you want to:
- Keep a service (e.g., HTTP server, database connection pool, ML model) running continuously
- Avoid cold-start penalties on every invocation
- Maintain state across multiple requests
- Simulate real production service behavior

## Use Cases

✅ **Use SERVICE_TASK when:**
- Running HTTP servers (FastAPI, Flask, etc.)
- Managing database connection pools
- Loading large ML models that should stay in memory
- Maintaining WebSocket connections
- Redis/cache clients with persistent connections
- External API clients with authentication/session state
- Any long-running service that's expensive to initialize

❌ **Don't use SERVICE_TASK when:**
- Tasks complete quickly and don't need to persist
- No state needs to be maintained between calls
- Resources should be released immediately after use

## Basic Example: Persistent HTTP Server

```python
from utils.simple_server import create_and_start_server

async def main():
    async with LangraphIntegration(backend=backend) as agents_manager:
        # Initialize the server once - this stays alive across SERVICE_TASK calls
        server = await create_and_start_server(host="localhost", port=8080)

        # SERVICE_TASK wraps the server to maintain continual uptime
        @agents_manager.execution_wrappers.asyncflow(
            flow_type=AsyncFlowType.SERVICE_TASK
        )
        async def call_server(endpoint: str, method: str = "GET") -> str:
            """Make a request to the persistent server."""
            result = await server.handle_request(endpoint, method)
            return format_response(result)

        # First request - server is already running
        result1, future1 = await call_server("/api/users", "GET")
        
        # Second request - SAME server instance (no cold-start!)
        result2, future2 = await call_server("/api/data", "POST")
        
        # Clean shutdown
        await server.shutdown()
```

**Key Characteristics:**
- ✅ Only ONE cold-start (server initialization)
- ✅ Same server instance handles all requests
- ✅ Request counter increments across calls
- ✅ Server uptime accumulates
- ✅ Returns `(result, future)` tuple for lifecycle control

## Production Patterns

### Pattern 1: Database Connection Pool
```python
# Initialize once
db_pool = await create_connection_pool(
    host="localhost",
    database="mydb",
    min_size=10,
    max_size=100
)

@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.SERVICE_TASK
)
async def query_db(sql: str):
    """Execute queries using persistent connection pool."""
    async with db_pool.acquire() as conn:
        return await conn.fetch(sql)

# All queries use the same connection pool
result1, future1 = await query_db("SELECT * FROM users")
result2, future2 = await query_db("SELECT * FROM products")
```

### Pattern 2: ML Model Server
```python
# Load model once (expensive operation)
model = await load_ml_model("path/to/large/model")

@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.SERVICE_TASK
)
async def predict(input_data: dict):
    """Make predictions using persistent model."""
    return await model.predict(input_data)

# No model reloading on subsequent calls
prediction1, future1 = await predict({"text": "sample input"})
prediction2, future2 = await predict({"text": "another input"})
```

### Pattern 3: Redis Cache Client
```python
# Initialize Redis connection once
redis_client = await create_redis_client(url="redis://localhost")

@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.SERVICE_TASK
)
async def cache_get(key: str):
    """Get value from persistent Redis connection."""
    return await redis_client.get(key)

# Same Redis connection for all operations
value1, future1 = await cache_get("user:123")
value2, future2 = await cache_get("session:456")
```

## Service Task as Agent Tool

For services that should be callable by agents, use `AGENT_TOOL_AS_SERVICE`:

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

## Lifecycle Management

SERVICE_TASK returns a `(result, future)` tuple, allowing you to control the service lifecycle:

```python
# Start service and make request
result1, future1 = await call_server("/api/users", "GET")

# Service continues running...
result2, future2 = await call_server("/api/data", "POST")

# Explicitly stop the service when needed
future1.cancel()
future2.cancel()
await server.shutdown()

# Next call will trigger a new cold-start
result3, future3 = await call_server("/api/health", "GET")  # New server instance
```

## Comparison: SERVICE_TASK vs FUNCTION_TASK

| Feature | FUNCTION_TASK | SERVICE_TASK |
|---------|---------------|--------------|
| Lifecycle | Task-scoped | Service-scoped |
| Cold-starts | Every call | Only on first call or restart |
| State | Not preserved | Preserved across calls |
| Return value | Result only | `(result, future)` tuple |
| Use case | Stateless operations | Stateful services |
| Overhead | Minimal | Startup cost amortized |

## Comparison: SERVICE_TASK vs AGENT_TOOL_AS_SERVICE

**Use SERVICE_TASK when:**
- Service used in workflow nodes
- Manual/direct function calls
- Don't need @tool wrapper
- Simple async function interface

**Use AGENT_TOOL_AS_SERVICE when:**
- Service should be callable by agents
- Need @tool wrapper for LLM invocation
- LLM decides when to invoke
- Tool needs to appear in agent's tool list

## Complete Examples

### Example 1: Persistent Service (No Cold-Start)
Demonstrates how SERVICE_TASK maintains continual uptime with no cold-starts.

```bash
python examples/langgraph-integration/service-task/service-task.py
```

**Expected Output:**
```
❄️  COLD START: Server starting...
Request 1 → Server A ✅
Wait 2 seconds...
Request 2 → Server A ✅ (No cold-start!)
Request 3 → Server A ✅ (No cold-start!)

Evidence:
- Same server instance ID across all requests
- Request counter: 1 → 2 → 3
- Server uptime accumulates: 2.2s
```

[View source code](https://github.com/stride-research/flowgentic/blob/main/examples/langgraph-integration/service-task/service-task.py)

### Example 2: Intermittent Service (Cold-Start on Restart)
Demonstrates how shutting down a server and canceling futures triggers a cold-start.

```bash
python examples/langgraph-integration/service-task/service-intermittent.py
```

**Expected Output:**
```
❄️  COLD START: Server A starting...
Request 1 → Server A ✅
Request 2 → Server A ✅
🛑 SHUTDOWN: Server A shutting down...
❄️  COLD START: Server B starting...
Request 3 → Server B ✅ (Different instance!)

Evidence:
- TWO cold-starts (different servers)
- Request counter reset: 2 → 1
- Different instance IDs
```

[View source code](https://github.com/stride-research/flowgentic/blob/main/examples/langgraph-integration/service-task/service-intermittent.py)

## Best practices
- Invoke a `service.shutdown()` before cancelling the future in order to have a graceful shutdown

## Key Takeaways

1. **SERVICE_TASK maintains state** - The service instance persists across multiple calls
2. **Avoid cold-starts** - Initialization happens once, not on every call
3. **Returns future** - You get `(result, future)` to control the service lifecycle
4. **Graceful shutdown** - Always call `server.shutdown()` and `future.cancel()` to clean up
5. **Production-ready** - This pattern mirrors real-world server/service behavior

## API Reference

For detailed API documentation, refer to: [Execution Wrappers API Reference](../../api/flowgentic/langGraph/execution_wrappers/)
