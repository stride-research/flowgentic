# Service Examples

Examples demonstrating **service=True** functionality for persistent service instances with continual uptime.

## Prerequisites

Before running these examples, you need to set up your environment:

### 1. Install Dependencies
```bash
pip install flowgentic python-dotenv
```

### 2. Set Up OpenRouter API Key

Create a `.env` file in the project root:
```bash
# .env
OPENROUTER_API_KEY=your_openrouter_api_key_here
```

Or set it as an environment variable:
```bash
export OPENROUTER_API_KEY=your_openrouter_api_key_here
```

Get your API key from: https://openrouter.ai/

---

## Overview

Both `SERVICE_TASK` and `AGENT_TOOL_AS_SERVICE` use the `service=True` parameter in AsyncFlow's `function_task()` to create persistent service instances that maintain state across multiple calls.

### Key Concept: Service Persistence

When `service=True` is used:
-  Service instance created **ONCE** on first call
-  Instance **persists** across multiple calls
-  State **accumulates** (connection pools, metrics, session data)
-  Maintains **continual uptime** until shutdown

**Use Cases:**
- Database connection pools
- HTTP client sessions
- API clients with authentication
- Message queue consumers
- WebSocket connections
- Background workers
- Stateful services (metrics, counters, session data)

**Not about caching results** - this is about keeping the service *instance* alive and maintaining any state it needs (connections, metrics, etc.).

---

## Examples

### Example 1: SERVICE_TASK
**File:** `01_service_task_example.py`

**Flow Type:** `AsyncFlowType.SERVICE_TASK`

**What it demonstrates:**
- Database service with persistent connection
- Service created on first call
- Agent uses service (query #1)
- 3 seconds pass (service stays alive)
- User manually calls service (query #2)
- **Proof:** Query count increments, uptime continues

**Key Code:**
```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.SERVICE_TASK
)
async def database_query_service(sql: str) -> str:
    # Lazy initialization
    if not hasattr(database_query_service, '_service'):
        database_query_service._service = DatabaseService()
    
    # Use persistent service instance
    return await database_query_service._service.execute_query(sql)
```

**Run it:**
```bash
python examples/service-task/01_service_task_example.py
```

**Expected Output:**
```
PART 1: Agent calls database_query_service
🚀 SERVICE_TASK: Database service instance created!
 Agent completed call #1

⏳ Waiting 3 seconds (service should stay alive)...

PART 2: User manually calls database_query_service
 Manual call completed
  Query #2
  Service Uptime: 3.2s    ← Proves service stayed alive!
```

---

### Example 2: AGENT_TOOL_AS_SERVICE
**File:** `02_agent_tool_as_service_example.py`

**Flow Type:** `AsyncFlowType.AGENT_TOOL_AS_SERVICE`

**What it demonstrates:**
- Weather API client with persistent session
- Wrapped as `@tool` for agent use
- Service created on first call
- Agent uses tool twice (requests #1 and #2)
- 3 seconds pass (service stays alive)
- User manually calls tool (request #3)
- **Proof:** Request count increments, session persists, uptime continues

**Key Code:**
```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.AGENT_TOOL_AS_SERVICE,
    tool_description="Weather API tool with persistent session"
)
async def weather_api_tool(city: str) -> str:
    # Lazy initialization
    if not hasattr(weather_api_tool, '_client'):
        weather_api_tool._client = WeatherAPIClient()
    
    # Use persistent service instance
    return await weather_api_tool._client.fetch_weather(city)

# Agent uses it automatically (wrapped as @tool)
agent = create_react_agent(model, tools=[weather_api_tool])

# Manual call requires .ainvoke() (LangChain tool convention)
result = await weather_api_tool.ainvoke({"city": "New York"})
```

**Important:** Since this is wrapped as `@tool`, manual calls must use `.ainvoke({"param": "value"})` syntax, not direct calls.

**Run it:**
```bash
python examples/service-task/02_agent_tool_as_service_example.py
```

**Expected Output:**
```
PART 1: Agent calls weather_api_tool
🚀 AGENT_TOOL_AS_SERVICE: Weather API client session created!
 Agent completed call #1 (San Francisco)
 Agent completed call #2 (Seattle)

⏳ Waiting 3 seconds (service should stay alive)...

PART 2: User manually calls weather_api_tool
 Manual call completed (New York)
  Request #3
  Session Uptime: 3.8s    ← Proves service stayed alive!
```

---

## Comparison: SERVICE_TASK vs AGENT_TOOL_AS_SERVICE

Both use `service=True`, the difference is in the wrapper:

| Aspect | SERVICE_TASK | AGENT_TOOL_AS_SERVICE |
|--------|--------------|----------------------|
| **Service persistence** |  Yes |  Yes |
| **Uses service=True** |  Yes |  Yes |
| **Maintains state** |  Yes |  Yes |
| **Wrapped as @tool** |  No |  Yes |
| **Agent-callable** |  Manual wiring |  Automatic |
| **Manual call syntax** | `await service("input")` | `await tool.ainvoke({"param": "value"})` |
| **Use case** | Nodes, manual calls | Agent tools |

**When to use SERVICE_TASK:**
- Direct function calls in workflow nodes
- Manual service invocation
- When you don't need @tool wrapper
- Call syntax: `await my_service("input")`

**When to use AGENT_TOOL_AS_SERVICE:**
- Service needs to be used by agents
- Want automatic @tool wrapping
- Agent should decide when to call it
- Call syntax: `await my_tool.ainvoke({"param": "value"})`

---

## How Service Persistence Works

### 1. Service Initialization (First Call)

```python
# First call to the service
result = await database_query_service("SELECT * FROM users")

# Behind the scenes:
if not hasattr(database_query_service, '_service'):
    database_query_service._service = DatabaseService()  # Created ONCE
    print("Service initialized!")

return await database_query_service._service.execute(...)
```

### 2. Service Reuse (Subsequent Calls)

```python
# Second call reuses the same service instance
result = await database_query_service("SELECT * FROM products")

# Behind the scenes:
# _service already exists! No reinitialization
return await database_query_service._service.execute(...)  # Same instance!
```

### 3. Continual Uptime

The service runs continuously:
- **Agent call #1:** Service initializes on first use
- **Agent call #2:** Same service instance responds
- **Wait 3 seconds:** Service stays alive
- **Manual call:** Same service instance responds
- **Proof:** Metrics show accumulated state (request count, uptime)

---

## Implementation Pattern

### Lazy Initialization

```python
@execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.SERVICE_TASK  # or AGENT_TOOL_AS_SERVICE
)
async def my_service(input: str):
    # Initialize on first call
    if not hasattr(my_service, '_service_instance'):
        my_service._service_instance = ExpensiveResource()
        print("Service initialized!")
    
    # Use the persistent instance
    return await my_service._service_instance.process(input)
```

### State Tracking

Track metrics to prove continual operation:

```python
class MyService:
    def __init__(self):
        self.created_at = datetime.now()
        self.request_count = 0
    
    async def process(self, input: str):
        self.request_count += 1
        uptime = (datetime.now() - self.created_at).total_seconds()
        
        return {
            "result": "...",
            "request_number": self.request_count,  # ← Proves accumulation
            "uptime": uptime                       # ← Proves continual operation
        }
```

---

## Under the Hood

Both examples use the same mechanism in `execution_wrappers.py`:

```python
elif flow_type in [
    AsyncFlowType.AGENT_TOOL_AS_SERVICE,
    AsyncFlowType.SERVICE_TASK,
]:
    # Uses service=True parameter
    asyncflow_func = self.flow.function_task(f, service=True)
```

The `service=True` parameter tells AsyncFlow's `function_task()` to:
1. Create the function task wrapper
2. Cache the service instance
3. Reuse the instance on subsequent calls
4. Maintain state across calls

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ SERVICE LIFECYCLE                                   │
├─────────────────────────────────────────────────────┤
│                                                     │
│  First Call (Agent or User):                        │
│  ┌────────┐         ┌──────────────────┐           │
│  │ Caller │────────>│ Check if service │           │
│  └────────┘         │ exists           │           │
│                     └──────────────────┘           │
│                              │ No                   │
│                              v                      │
│                     ┌──────────────────┐           │
│                     │ Initialize       │           │
│                     │ Service Instance │           │
│                     └──────────────────┘           │
│                              │                      │
│                              v                      │
│                     ┌──────────────────┐           │
│                     │ Cache Instance   │           │
│                     │ on Function      │           │
│                     └──────────────────┘           │
│                              │                      │
│                              v                      │
│                     ┌──────────────────┐           │
│                     │ Execute Request  │           │
│                     └──────────────────┘           │
│                                                     │
│  Subsequent Calls:                                  │
│  ┌────────┐         ┌──────────────────┐           │
│  │ Caller │────────>│ Check if service │           │
│  └────────┘         │ exists           │           │
│                     └──────────────────┘           │
│                              │ Yes!                 │
│                              v                      │
│                     ┌──────────────────┐           │
│                     │ Reuse Cached     │           │
│                     │ Service Instance │           │
│                     └──────────────────┘           │
│                              │                      │
│                              v                      │
│                     ┌──────────────────┐           │
│                     │ Execute Request  │           │
│                     │ (State persists!)│           │
│                     └──────────────────┘           │
└─────────────────────────────────────────────────────┘
```

---

## Best Practices

### 1. Lazy Initialization
 Initialize service on first call, not at import time
```python
# Good
if not hasattr(my_service, '_instance'):
    my_service._instance = Service()

# Bad
_instance = Service()  # Created at import, before needed
```

### 2. State Tracking
 Track metrics to prove continual operation
```python
class Service:
    def __init__(self):
        self.created_at = datetime.now()
        self.request_count = 0
```

### 3. Error Handling
 Handle service failures gracefully
```python
try:
    return await service._instance.process(input)
except ConnectionError:
    # Optionally reinitialize service
    del service._instance
    raise
```

### 4. Cleanup
 Implement proper shutdown for production
```python
async def shutdown():
    if hasattr(my_service, '_instance'):
        await my_service._instance.close()
```

## Troubleshooting

### Error: "OpenRouter API key not provided"

**Problem:** Missing `.env` file or environment variable.

**Solution:** Create `.env` file:
```bash
# .env
OPENROUTER_API_KEY=your_api_key_here
```

### Service Not Persisting

**Problem:** Service recreated on each call.

**Solution:** Check you're using `service=True` in the flow type:
- Use `AsyncFlowType.SERVICE_TASK` (has service=True)
- Or use `AsyncFlowType.AGENT_TOOL_AS_SERVICE` (has service=True)
- NOT `AsyncFlowType.FUNCTION_TASK` (no service persistence)

---

## Next Steps

1. **Run both examples** to see services in action
2. **Modify state tracking** - add more metrics to prove persistence
3. **Implement real services** - try with `asyncpg`, `aiohttp`, etc.
4. **Add cleanup handlers** - implement proper shutdown for production
5. **Build your own** - use the pattern for your use case

---

## Key Takeaways

 **Both SERVICE_TASK and AGENT_TOOL_AS_SERVICE use service=True**
 **Service instances persist across multiple calls**
 **State accumulates** (metrics, cache, connections)
 **Continual uptime** proven by metrics
 **Choose based on usage**: Direct calls vs Agent tools