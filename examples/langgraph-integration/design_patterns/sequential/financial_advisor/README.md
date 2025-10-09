# Financial Advisory Sequential Workflow (LangGraph)

A sophisticated sequential (pipeline) example that builds a multi-agent financial advisory workflow using FlowGentic's LangGraph integration. It validates user intent, performs market research, assesses risk and compliance, synthesizes a portfolio strategy, and formats a final report with full telemetry.

## What this example shows

- Sequential, stage-by-stage orchestration with explicit state hand-off
- Three LLM agents with tools:
  - Market Research Agent (market data, sector trends, macro indicators)
  - Risk Assessment Agent (risk metrics, compliance checks)
  - Portfolio Strategy Agent (allocation optimization, recommendations, report doc)
- Deterministic context-prep and formatting tasks between agent stages
- Full introspection and execution report for multi-agent runs

## How to run

```bash
make examples-sequential-financial
```

This will:
- Build and execute the workflow
- Stream stage updates to the console
- Generate telemetry artifacts under `agent_execution_results/`:
  - `execution_summary.md` (detailed run report)
  - `agent_graph.png` (graph visualization)

Note: The example uses mocked tool logic for external data (market data, trends, etc.). LLM models are invoked via FlowGentic's `ChatLLMProvider`. Ensure your environment is configured for your chosen provider if required.

## File structure

- `main.py` — Orchestrates the app lifecycle and streaming
- `components/builder.py` — Assembles nodes, edges, and wiring
- `components/nodes.py` — All workflow nodes (validation → research → risk → strategy → finalize)
- `components/edges.py` — Conditional routing logic
- `components/utils/actions.py` — Agent tools and deterministic tasks (mocked)
- `components/utils/actions_registry.py` — Centralized registry for tools/tasks
- `utils/schemas.py` — Pydantic state and data models

## High-level flow

1) Validate input → 2) Market research agent → 3) Prepare risk context →
4) Risk assessment agent → 5) Prepare strategy context →
6) Portfolio strategy agent → 7) Finalize report

Each stage updates the shared `WorkflowState`, enabling clear debugging and rich introspection.
