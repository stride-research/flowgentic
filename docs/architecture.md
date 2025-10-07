# Architecture

Flowgentic provides a thin, opinionated layer that standardizes agent components and lets you run the same logic on multi-agent orchestration frameworks (e.g. LangGraph) on HPC workflow engines (e.g. Radical AsyncFlow).

## Architecture: Execution Bridge Pattern

```mermaid
flowchart TD
    subgraph HighLevel["High-Level: Agent Framework"]
        A[Agent Graph Definition]
        B[Agent Nodes & Tools]
        C[Conditional Routing]
    end
    
    subgraph MiddleLayer["Middle Layer: FlowGentic Abstraction"]
        D[Graph Compiler]
        E[Node to Task Translation]
        F[State Serialization]
        G[Dependency Resolution]
    end
    
    subgraph LowLevel["Low-Level: HPC Execution Backend"]
        H[HPC Task Scheduler]
        I[Parallel Execution Units]
        J[Distributed Memory/State]
        K[Resource Manager]
    end
    
    A --> D
    B --> E
    C --> G
    
    D --> E
    E --> F
    F --> G
    
    G --> H
    H --> I
    I --> J
    J --> K
    
    K -.->|Results| G
    G -.->|State Updates| F
    F -.->|Checkpoints| A
```

## Wrappers
```mermaid
flowchart TD
    subgraph "ASYNCFLOW WRAPPERS"
        A[EXECUTION_BLOCK]
        subgraph ACTIONS
            direction LR
            B[AGENT TOOLS]
            C[UTIL TASKS]
            B --> D[AGENT_TOOL_AS_FUNCTION]
            B --> E[AGENT_TOOL_AS_MCP]
            B --> F[AGENT_TOOL_AS_SERVICE]
            C --> G[TASK_FUNCTION]
            C --> H[TASK_SERVICE]
        end
        
        A -- BLOCKS --> ACTIONS
        ACTIONS --> B
        ACTIONS --> C
    end
```