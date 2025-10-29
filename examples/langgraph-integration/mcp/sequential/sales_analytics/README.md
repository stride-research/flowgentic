# Sales Analytics Sequential Workflow (MCP Integration)

A sophisticated sequential (pipeline) example demonstrating FlowGentic's MCP integration using the official `langchain-mcp-adapters` library. It validates queries, extracts data via MCP database specialist, performs statistical analysis, generates reports, and provides full telemetry.

## What this example shows

- Sequential, stage-by-stage orchestration with explicit state hand-off
- Three LLM agents with tools:
  - Data Extraction Agent (MCP SQLite database specialist)
  - Analytics Agent (growth rates, anomaly detection, performance metrics, trends)
  - Report Generation Agent (executive summaries, visualizations, recommendations)
- Deterministic context-prep and formatting tasks between agent stages
- MCP integration with automatic tool discovery from SQLite MCP server
- Full introspection and execution report for multi-agent runs

## How to run

### Prerequisites
```bash
# Install library to use MCP tools in LangGraph
pip install langchain-mcp-adapters
```

### Run the example
```bash
make examples-mcp-sales-analytics
```

This will:
- Build and execute the workflow
- Stream stage updates to the console
- Generate telemetry artifacts under `agent_execution_results/`:
  - `execution_summary.md` (detailed run report with token usage, execution times)
  - `agent_graph.png` (graph visualization)

Note: The MCP database specialist uses the `mcp-sqlite` server (Python-based SQLite MCP server via uvx). The database is created with sample sales data (500 records across 4 regions). LLM models are invoked via FlowGentic's `ChatLLMProvider`. Ensure your environment is configured with API keys.

## File structure

- `setup_database.py` — Creates SQLite database with sample sales data
- `main.py` — Orchestrates the app lifecycle and streaming
- `components/builder.py` — Assembles nodes, edges, and wiring
- `components/nodes.py` — All workflow nodes (validation → extraction → analytics → report → finalize)
- `components/edges.py` — Conditional routing logic
- `components/utils/actions.py` — Agent tools and deterministic tasks (MCP + analytics + report)
- `components/utils/actions_registry.py` — Centralized registry for tools/tasks
- `utils/schemas.py` — Pydantic state and data models

## High-level flow

1) Validate query → 2) Data extraction agent (MCP) → 3) Prepare analytics context →
4) Analytics agent → 5) Prepare report context →
6) Report generation agent → 7) Finalize report

Each stage updates the shared `WorkflowState`, enabling clear debugging and rich introspection.

## MCP Integration

This example demonstrates the **hybrid execution model**:
- **MCP server runs locally** (mcp-sqlite via uvx subprocess)
- **Agent execution goes through AsyncFlow** (HPC benefits: retry, monitoring, parallel execution)

The database specialist agent automatically discovers SQL execution tools from the MCP server and uses them intelligently to query sales data.