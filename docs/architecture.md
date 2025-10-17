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
        subgraph SERVICES["SERVICE PATTERNS"]
            direction LR
            B[SERVICE_TASK<br/>Persistent Internal Services]
            C[TOOL_AS_SERVICE<br/>LLM-Callable Services]
        end
        
        subgraph TOOLS["AGENT TOOLS"]
            direction LR
            D[AGENT_TOOL_AS_FUNCTION<br/>Simple Tools]
            E[AGENT_TOOL_AS_MCP<br/>MCP Integration]
        end
        
        subgraph TASKS["FUNCTION TASKS"]
            direction LR
            F[FUNCTION_TASK<br/>Deterministic Operations]
        end
        
        A -- ORCHESTRATES --> SERVICES
        A -- ORCHESTRATES --> TOOLS
        A -- ORCHESTRATES --> TASKS
    end
    
    SERVICES -.->|Caching| B
    SERVICES -.->|LLM Calls| C
    TOOLS -.->|Direct Call| D
    TOOLS -.->|External Server| E
    TASKS -.->|Pure Functions| F
    
    style B fill:#e1f5e1
    style C fill:#e1f5e1
    style D fill:#e1e8f5
    style E fill:#e1e8f5
    style F fill:#f5e1e1
```

### Flow Type Categories

**Service Patterns** (Persistent, Stateful)
- `SERVICE_TASK`: Internal services with caching (database pools, Redis clients)
- `TOOL_AS_SERVICE`: LLM-callable services with caching (Weather APIs, search tools)

**Agent Tools** (LLM-Callable)
- `AGENT_TOOL_AS_FUNCTION`: Simple synchronous tools for LLMs
- `AGENT_TOOL_AS_MCP`: External MCP server integration

**Function Tasks** (Deterministic)
- `FUNCTION_TASK`: Pure deterministic operations (validation, formatting)

All wrapped by:
- `EXECUTION_BLOCK`: LangGraph nodes (orchestration layer)