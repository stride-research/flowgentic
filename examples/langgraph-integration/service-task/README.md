# SERVICE_TASK: Persistent Background Services in AsyncFlow

This directory demonstrates the **SERVICE_TASK** capability in AsyncFlow, which allows you to start and manage persistent background services within your LangGraph workflows.

## 🎯 What is SERVICE_TASK?

`SERVICE_TASK` is a special AsyncFlow decoration type that creates **persistent, long-running services** that:
- Start once and run in the background
- Can be accessed multiple times without restarting
- Maintain state across multiple operations
- Run until explicitly cancelled or shutdown

## 🔄 How It Works

### Key Behavior Differences

| Aspect | FUNCTION_TASK | SERVICE_TASK |
|--------|---------------|--------------|
| **Returns** | Awaited result | Future handle |
| **Execution** | Runs to completion | Starts and persists |
| **Reuse** | Creates new execution each time | Same instance on reuse |
| **State** | Stateless | Maintains state |
| **Lifecycle** | Automatic | Manual management |

### Usage Pattern

```python
# 1. Define a SERVICE_TASK
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.SERVICE_TASK
)
async def start_database_pool(connection_string: str):
    """Starts a persistent database connection pool."""
    pool = await create_pool(connection_string)
    return pool

# 2. Start the service (get future handle)
service_future = await start_database_pool("postgresql://...")

# 3. Get the service instance
db_pool = await service_future

# 4. Use the service multiple times
result1 = await db_pool.execute("SELECT * FROM users")
result2 = await db_pool.execute("SELECT * FROM orders")
# ... same pool, no reconnection!

# 5. Clean up when done
service_future.cancel()
await db_pool.close()
```

## 📊 Proof Points from the Example

The `service-intermittent.py` example demonstrates:

### ✅ 1. **Service Persistence**
- Same future = Same service instance
- Request counter increments: 1 → 2 → 3 → 4
- Server uptime increases continuously
- Only ONE "COLD START" message

### ✅ 2. **New Calls Create New Instances**
- Different service call = Different instance
- Counter resets to 1
- New server ID generated
- SECOND "COLD START" message appears

### ✅ 3. **Concurrent Services**
- Multiple services run independently
- Each maintains separate state
- Each has independent uptime counter
- Both respond to requests simultaneously

### ✅ 4. **Proper Lifecycle Management**
- Services run until explicitly cancelled
- `future.cancel()` stops the service
- Cleanup can be performed gracefully
- Resources are properly released

## 🎮 Running the Example

```bash
cd /path/to/flowgentic
source .venv/bin/activate
python examples/langgraph-integration/service-task/service-intermittent.py
```

## 🔍 Understanding the Output

### Part 1: Single Service Persistence
```
🔷 Step 1: Start first service
   → Got service future handle: <class '_asyncio.Future'>

🔷 Step 2: Await the future to get server instance
   → Server instance: server_1762983549724

🔷 Step 3-5: Multiple requests to same server
   📡 Request #1  (Uptime: 0.10s)
   📡 Request #2  (Uptime: 0.61s)
   📡 Request #3  (Uptime: 1.13s)
```
**Proof**: Same server ID, incrementing counter, increasing uptime = persistent service!

### Part 2: New Service Call
```
🔷 Step 6: Start SECOND service
   🚀 COLD START (second time!)
   → Server instance: server_1762983550863

🔷 Step 7: Request to second server
   📡 Request #1  (Counter RESET!)
```
**Proof**: Different server ID, reset counter = new instance!

### Part 3: Concurrent Services
```
🔷 Step 8: Request to FIRST server
   📡 Request #4  (Counter continued from 3!)

🔷 Step 9: Request to SECOND server
   📡 Request #2  (Independent counter!)
```
**Proof**: Both services running independently with separate state!

## 💡 Real-World Use Cases

### 1. **Database Connection Pools**
```python
@asyncflow(flow_type=AsyncFlowType.SERVICE_TASK)
async def start_db_pool(db_url: str):
    return await asyncpg.create_pool(db_url, min_size=5, max_size=20)
```

### 2. **ML Model in Memory**
```python
@asyncflow(flow_type=AsyncFlowType.SERVICE_TASK)
async def load_ml_model(model_path: str):
    # Load heavy model once, keep in memory
    model = await load_transformers_model(model_path)
    return model
```

### 3. **WebSocket Connections**
```python
@asyncflow(flow_type=AsyncFlowType.SERVICE_TASK)
async def open_websocket(url: str):
    ws = await websockets.connect(url)
    return ws
```

### 4. **Cache Servers (Redis)**
```python
@asyncflow(flow_type=AsyncFlowType.SERVICE_TASK)
async def connect_redis(host: str, port: int):
    redis = await aioredis.create_redis_pool(f'redis://{host}:{port}')
    return redis
```

### 5. **API Rate-Limited Clients**
```python
@asyncflow(flow_type=AsyncFlowType.SERVICE_TASK)
async def create_api_client(api_key: str):
    # Maintains rate limit state across requests
    client = RateLimitedAPIClient(api_key, max_requests_per_minute=60)
    return client
```

## 🚨 Important Notes

### ❌ Common Mistakes

**Wrong**: Expecting a result directly
```python
result = await start_service()  # This is a FUTURE, not the result!
```

**Right**: Get the future, then await it
```python
service_future = await start_service()  # Get the handle
service = await service_future           # Get the instance
```

### ⚠️ Lifecycle Management

**Always clean up services:**
```python
try:
    service_future = await start_service()
    service = await service_future
    # ... use service ...
finally:
    service_future.cancel()
    await service.cleanup()
```

### 🔒 Thread Safety

If your service uses threads (like the example server), ensure proper:
- Lock management
- State synchronization
- Graceful shutdown

## 🆚 When to Use Each Type

| Use Case | FUNCTION_TASK | SERVICE_TASK |
|----------|---------------|--------------|
| One-off computation | ✅ | ❌ |
| Stateless API call | ✅ | ❌ |
| Database connection pool | ❌ | ✅ |
| ML model in memory | ❌ | ✅ |
| WebSocket connection | ❌ | ✅ |
| File processing | ✅ | ❌ |
| Long-running server | ❌ | ✅ |

## 📚 Key Takeaways

1. **SERVICE_TASK returns a future handle**, not the result
2. **Await the future to get the actual service instance**
3. **Same future = same service** (no restarts)
4. **Multiple services can run concurrently** with independent state
5. **Always manage lifecycle** with proper cleanup
6. **Use for expensive resources** you want to initialize once and reuse

## 🔗 Related

- [Fault Tolerance Documentation](../../../docs/features/fault_tolerance.md)
- [Memory Management](../../../docs/features/memory.md)
- [Dynamic Graph Creation](../../../docs/features/dynamic-graph.md)

