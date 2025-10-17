# SERVICE_TASK

Persistent internal services that initialize once and cache for all future calls.

- **One-time initialization**: Connect to databases, APIs, or external services once
- **Automatic caching**: Service instances persist across multiple invocations
- **Resource efficiency**: Eliminate repeated expensive connection setup
- **Type safety**: Full Pydantic integration for service configuration

## Use Cases

- Database connection pools
- Redis/cache clients
- External API clients with authentication
- Background processing services
- Any stateful service that should persist

## Example
```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.SERVICE_TASK
)
async def database_pool():
    """Initialize and return a database connection pool"""
    # Expensive one-time setup
    pool = DatabasePool(connections=10)
    await pool.connect()
    return pool

# First call: initializes and caches
db = await database_pool()
# Subsequent calls: returns cached instance (instant)
db = await database_pool()
```

## How It Works

1. First invocation triggers initialization
2. Service instance cached in memory
3. Future calls return cached instance immediately
4. No re-initialization overhead

## Examples

- [Basic SERVICE_TASK example](https://github.com/stride-research/flowgentic/blob/main/examples/langgraph-integration/06-service-task-example.py)

## API

Refer to the execution wrappers API: [API Reference](../../api/flowgentic/langGraph/execution_wrappers/)