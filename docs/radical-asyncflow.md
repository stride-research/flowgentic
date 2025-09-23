

# DECORATORS
- Block => fan-out / fin-in. Grouping several parallel stuff that needs to be await at a given checkpoint. 
- Function task => defining steps of workflow. Build stuff indepedently. For instance: creating ageny. FUNCTION TO BE EXECUTED IN HPC THAT DOESNT BELONG TO THE AGENT
- Langraph integration => agent tools 

# DEV DOCUMENTATION
## @integration.asyncflow_tool
Conceptual Role
A Primitive Action or Capability available to an agent.

Architectural Layer
The Agent-Environment Interface Layer. This decorator is responsible for bridging the agent's internal reasoning with external systems or data sources.

Formal Definition
The @integration.asyncflow_tool decorator promotes a standard Python function to a formally defined action within an agent's operational space. It serves as a wrapper that endows a function with non-functional properties essential for robust agentic execution, primarily fault tolerance and predictable performance.

By specifying configurations for retries, backoff strategies, and execution timeouts, it transforms a simple callable into a resilient capability. This allows the agent's planning and reasoning components to invoke the tool without being tightly coupled to the implementation details of error handling for external I/O or stochastic API behaviors. The decorator effectively makes the function a managed endpoint in the agent's environment interaction model.

## @flow.function_task
# AsyncFlow + LangGraph Integration

## Problem: Execution Context Mismatch

When integrating AsyncFlow tasks as LangGraph nodes, you'll encounter a runtime error:

```
RuntimeError: There is no current event loop in thread 'asyncio_0'
```

### Root Cause

**LangGraph's Node Execution Model:**
- Runs nodes in thread pool executors by default
- Uses `asyncio.run_in_executor()` to prevent blocking the main async loop
- Threads don't have their own event loop

**AsyncFlow's Requirements:**
- Decorators (`@flow.function_task`, `@flow.executable_task`) create `asyncio.Future` objects
- Requires an active event loop to manage async task lifecycle
- Needs async context for dependency tracking and state management

### The Conflict

```python
# This fails:
@flow.function_task
async def my_task():
    return "result"

workflow.add_node("my_node", my_task)  # Runs in thread → no event loop → crash
```

## Solution: Node Wrapper Pattern

Create an async wrapper that bridges the execution contexts:

```python
@agents_manager.agents.flow.function_task
async def asyncflow_task():
    # Your AsyncFlow task logic
    return {"status": "completed"}

async def langgraph_node_wrapper(state: WorkflowState):
    """Wrapper that executes AsyncFlow task in proper async context"""
    try:
        # Execute the AsyncFlow task (this runs in main async context)
        task_future = asyncflow_task()
        result = await task_future
        
        # Return LangGraph state object
        return state
    except Exception as e:
        logger.error(f"AsyncFlow task failed: {e}")
        return state  # Always return state to prevent workflow failure

# Use the wrapper, not the raw task
workflow.add_node("my_node", langgraph_node_wrapper)
```

## Why This Works

1. **LangGraph executes the wrapper** in a thread pool
2. **Wrapper calls back** to the main async context where AsyncFlow task runs
3. **AsyncFlow task has access** to the event loop it needs
4. **Wrapper handles** the context translation between systems

## Alternative: Utility Class

For reusability across multiple tasks:

```python
class AsyncFlowNodeWrapper:
    @staticmethod
    def wrap(asyncflow_task: Callable) -> Callable:
        async def wrapper(state):
            try:
                task_future = asyncflow_task()
                result = await task_future
                return state
            except Exception as e:
                logger.error(f"AsyncFlow task error: {e}")
                return state
        return wrapper

# Usage:
workflow.add_node("task", AsyncFlowNodeWrapper.wrap(my_asyncflow_task))
```

## Best Practices

### ✅ Do
- Always wrap AsyncFlow tasks when using as LangGraph nodes
- Include error handling in wrappers to prevent workflow crashes
- Return the state object even on task failure
- Use descriptive wrapper function names for debugging

### ❌ Don't
- Add AsyncFlow decorated functions directly as LangGraph nodes
- Forget error handling in wrapper functions
- Assume LangGraph will automatically handle async context

## Architecture Benefits

This pattern maintains:
- **Unified execution**: All tasks flow through AsyncFlow system
- **Observability**: AsyncFlow dependency tracking and profiling
- **Error handling**: Consistent error handling across all tasks
- **Resource management**: AsyncFlow concurrency limits and retries

## Debugging Tips

If you see the event loop error:
1. Check if you're adding a decorated AsyncFlow function directly to LangGraph
2. Verify your wrapper is properly async and calls the task correctly
3. Ensure the wrapper returns a valid state object
4. Add logging to trace execution flow between systems

## @flow.block
Conceptual Role
A Composite Task or Orchestration Logic.

Architectural Layer
The Workflow Composition Layer. This decorator operates at a higher level of abstraction, defining the control flow and data dependencies between multiple function_task units.

Formal Definition
The @flow.block decorator encapsulates a sequence of function_task invocations into a single, logical sub-workflow. It allows developers to define complex interaction patterns—such as parallel fan-out/fan-in, sequential pipelines, and conditional execution—using standard Python async/await syntax. The await keyword within a block signals a dependency in the computational graph, instructing the WorkflowEngine to wait for the result of one or more tasks before proceeding.

This enables hierarchical workflow composition, where a complex process can be broken down into a structured block of simpler tasks. From the perspective of the broader workflow, the entire block can be treated as a single computational node, abstracting away its internal complexity. This simplifies the design and maintenance of large-scale, multi-agent systems.

Summary of Architectural Layers
The three decorators operate at distinct, hierarchical layers of an agentic system's architecture, moving from external interaction to internal orchestration.

@integration.asyncflow_tool (Interface Layer): Defines the outermost boundary—the agent's atomic capabilities for interacting with its environment.

@flow.function_task (Execution Layer): Defines the core, parallelizable units of work or reasoning that use the tools.

@flow.block (Composition Layer): Defines the high-level logic that coordinates the execution of core tasks to achieve a composite goal.