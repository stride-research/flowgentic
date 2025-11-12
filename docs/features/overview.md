# Overview

Quick links to core features:

- [Memory](memory.md)
- [Fault tolerance](fault_tolerance.md)
- [Mutable Graph](mutable_graph.md)
- [Telemetry](telemetry.md)
- [Services](services.md)
- [MCP](mcp.md)
- [Miscellaneous](miscellaneous.md)

```mermaid
graph TD
  A[Flowgentic Features] --> B[Memory]
  A --> C[Fault tolerance]
  A --> D[Telemetry]
  A --> E[Services]
  A --> F[MCP]
  A --> G[Mutable Graph]
  A --> H[Miscellaneous]

  B --> B1[Stateful context]
  C --> C1[Retries & Backoff]
  C --> C2[State persistence]
  D --> D1[HPC-ready metrics]
  E --> E1[Continual Uptime]
  F --> F1[MCP servers and clients]
  G --> G1[Mutable Graph]
  H --> H1[LLM providers]
  H --> H2[Facade setup]
```
