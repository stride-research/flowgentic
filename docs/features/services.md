# SERVICE_TASK

Persistent internal services that initialize once and maintain continual uptime across multiple invocations.

- **One-time initialization**: Connect to databases, APIs, or external services once
- **Service persistence**: Service instances stay alive across multiple invocations
- **Continual uptime**: Maintain connections, state, and resources without cold-starts
- **Resource efficiency**: Eliminate repeated expensive connection setup
- **Future control**: Returns a future handle that yields the service instance

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
from utils.simple_server import SimpleAsyncServer

async def main():
    async with LangraphIntegration(backend=backend) as agents_manager:
        # SERVICE_TASK wraps the server to maintain continual uptime
        @agents_manager.execution_wrappers.asyncflow(
            flow_type=AsyncFlowType.SERVICE_TASK
        )
        async def start_server(port: int) -> SimpleAsyncServer:
            """Start a persistent background server."""
            print("🚀 COLD START: Initializing server...")
            server = SimpleAsyncServer(host="localhost", port=port)
            await server.start()
            return server

        # First call returns a future handle
        service_future = await start_server(8080)
        
        # Await the future to get the actual server instance
        server = await service_future
        
        # Make requests - SAME server instance (no cold-start!)
        result1 = await server.handle_request("/api/users", "GET")
        result2 = await server.handle_request("/api/data", "POST")
        
        # Clean shutdown
        service_future.cancel()
        await server.shutdown()
```

**Key Characteristics:**
- ✅ Only ONE cold-start (server initialization)
- ✅ Same server instance handles all requests
- ✅ Request counter increments across calls
- ✅ Server uptime accumulates
- ✅ Returns future handle that yields the service instance

## Production Patterns

### Pattern 1: Database Connection Pool
```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.SERVICE_TASK
)
async def create_db_service():
    """Initialize persistent database connection pool."""
    db_pool = await create_connection_pool(
        host="localhost",
        database="mydb",
        min_size=10,
        max_size=100
    )
    return db_pool

# Start the service and get the pool
service_future = await create_db_service()
db_pool = await service_future

# All queries use the same connection pool
async with db_pool.acquire() as conn:
    result1 = await conn.fetch("SELECT * FROM users")
    
async with db_pool.acquire() as conn:
    result2 = await conn.fetch("SELECT * FROM products")

# Cleanup
service_future.cancel()
await db_pool.close()
```

### Pattern 2: ML Model Server
```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.SERVICE_TASK
)
async def load_model_service():
    """Load model once (expensive operation)."""
    model = await load_ml_model("path/to/large/model")
    return model

# Start the service and get the model
service_future = await load_model_service()
model = await service_future

# No model reloading on subsequent calls
prediction1 = await model.predict({"text": "sample input"})
prediction2 = await model.predict({"text": "another input"})

# Cleanup
service_future.cancel()
```

### Pattern 3: Redis Cache Client
```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.SERVICE_TASK
)
async def create_redis_service():
    """Initialize Redis connection once."""
    redis_client = await create_redis_client(url="redis://localhost")
    return redis_client

# Start the service and get the client
service_future = await create_redis_service()
redis_client = await service_future

# Same Redis connection for all operations
value1 = await redis_client.get("user:123")
value2 = await redis_client.get("session:456")

# Cleanup
service_future.cancel()
await redis_client.close()
```

## Lifecycle Management

SERVICE_TASK returns a future handle that you await to get the service instance:

```python
# Start service and get the future handle
service_future = await start_server(8080)

# Await the future to get the service instance
server = await service_future

# Make requests - service continues running...
result1 = await server.handle_request("/api/users", "GET")
result2 = await server.handle_request("/api/data", "POST")

# Explicitly stop the service when needed
service_future.cancel()
await server.shutdown()

# Starting a new service will trigger a cold-start
service_future2 = await start_server(8081)  # New server instance
server2 = await service_future2
```

## Comparison: SERVICE_TASK vs FUNCTION_TASK

| Feature | FUNCTION_TASK | SERVICE_TASK |
|---------|---------------|--------------|
| Lifecycle | Task-scoped | Service-scoped |
| Cold-starts | Every call | Only on first call or restart |
| State | Not preserved | Preserved across calls |
| Return value | Result only | Future handle |
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

## Complete Example

### Persistent Service Behavior Demo
Demonstrates the complete SERVICE_TASK lifecycle including:
- **Part 1**: Single service instance with no cold-starts across multiple requests
- **Part 2**: New service call creates a new instance (cold-start)
- **Part 3**: Multiple services running concurrently
- **Part 4**: Graceful shutdown and cleanup

```bash
python examples/langgraph-integration/service-task/service-intermittent.py
```

**Key Demonstrations:**
```
Part 1: Persistence Proof
  ❄️  COLD START: Server 1 starting...
  Request 1 → Server 1 ✅
  Request 2 → Server 1 ✅ (No cold-start!)
  Request 3 → Server 1 ✅ (No cold-start!)

Part 2: New Service Cold-Start
  ❄️  COLD START: Server 2 starting...
  Request 1 → Server 2 ✅ (Different instance!)

Part 3: Concurrent Services
  Request 4 → Server 1 ✅ (Still running!)
  Request 2 → Server 2 ✅ (Also running!)

Evidence:
  - Same future handle = Same service instance
  - Different futures = Different instances (with cold-starts)
  - Request counters increment independently per service
  - Both services maintain separate state and uptime
```

[View source code](https://github.com/stride-research/flowgentic/blob/main/examples/langgraph-integration/service-task/service-intermittent.py)

## Best Practices
- Cancel the future with `service_future.cancel()` and then call `await service.shutdown()` for graceful cleanup
- Always await the service future to get the actual service instance before making requests
- Store the future handle if you need to cancel the service later

## Key Takeaways

1. **SERVICE_TASK maintains state** - The service instance persists across multiple calls
2. **Avoid cold-starts** - Initialization happens once, not on every call
3. **Returns future handle** - First await returns a future, second await gets the service instance
4. **Graceful shutdown** - Always call `future.cancel()` and `server.shutdown()` to clean up
5. **Production-ready** - This pattern mirrors real-world server/service behavior

## API Reference

For detailed API documentation, refer to: [Execution Wrappers API Reference](../../api/flowgentic/langGraph/execution_wrappers/)
