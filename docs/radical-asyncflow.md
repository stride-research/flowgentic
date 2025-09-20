

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
Conceptual Role
An Autonomous Computational Unit or Agentic Process.

Architectural Layer
The Workflow Execution Core Layer. These tasks represent the fundamental nodes in the computational Directed Acyclic Graph (DAG) managed by the workflow engine.

Formal Definition
The @flow.function_task decorator designates a function or coroutine as an independently schedulable unit of computation. Invoking a function decorated as a function_task does not result in its immediate execution. Instead, it registers the function and its parameters as a task with the WorkflowEngine and returns an asyncio.Future object. This future acts as a placeholder or handle for the eventual result of the computation.

This abstraction is the primary mechanism for achieving task-level parallelism. It allows the system to construct a graph of computational dependencies and execute independent tasks concurrently on a backend executor. Each function_task should encapsulate a logically cohesive operation, such as the entire reasoning loop of a specialized agent or a significant data transformation step.

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