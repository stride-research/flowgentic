# LangGraph vs Radical AsyncFlow

Flowgentic abstracts the differences so you keep your agent code and swap only the runner.

## Minimal overhead

- Shared node and state interfaces
- Sequential/branching graphs map directly to AsyncFlow tasks
- Logging/memory/fault APIs stay the same

## When to choose each

- LangGraph: rapid prototyping, single-host runs, notebooks
- AsyncFlow: HPC scaling, batch scheduling, distributed execution

## Why Flowgentic is better

- One codebase for both worlds
- Pragmatic primitives for production
- Examples and docs for common patterns
