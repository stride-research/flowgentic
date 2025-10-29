# Product Research Assistant - Advanced Supervisor Pattern (LangGraph)

An advanced supervisor pattern example that builds a multi-agent product research workflow with parallel execution and conditional synthesis. It routes queries to specialized LLM agents, detects audience context, and generates tailored reports using different synthesizer strategies.

## What this example shows

- Dynamic LLM-based routing to multiple specialized agents
- Parallel execution of worker agents with real LLM reasoning
- Mock tools for product specifications and user reviews search
- Context-aware audience detection (technical vs consumer)
- Conditional synthesis routing to different report generators:
  - Technical Synthesizer (for developers, engineers, professionals)
  - Consumer Synthesizer (for general buyers, everyday users)
- Two-stage routing architecture (workers → synthesizers)
- Full introspection and execution telemetry

## How to run

```bash
make examples-supervisor-product-research
```

This will:
- Execute the workflow with a test query
- Show parallel agent execution in real-time
- Generate telemetry artifacts under `agent_execution_results/`:
  - `execution_summary.md` (detailed run report with timing)
  - `agent_graph.png` (graph visualization)

Note: The example uses mocked tools for product data search. LLM models are invoked via FlowGentic's `ChatLLMProvider` (currently using Google Gemini via OpenRouter).

## Architecture overview

The supervisor pattern with conditional synthesis:

```
START
  ↓
llm_router (decides which workers to invoke)
  ↓
[technical_specs_agent || user_reviews_agent] (parallel LLM agents with tools)
  ↓
gather_results (collects outputs + detects audience type)
  ↓
synthesis_router (decides which synthesizer to use)
  ↓
[technical_synthesizer OR consumer_synthesizer]
  ↓
END
```

## Key components

**Worker Agents** (run in parallel):
- `technical_specs_agent` — Analyzes product specifications using mock search tool
- `user_reviews_agent` — Analyzes user reviews and sentiment using mock search tool

**Routers**:
- `llm_router` — LLM decides which worker agents to invoke based on query
- `synthesis_router` — Routes to appropriate synthesizer based on audience

**Synthesizers** (conditional execution):
- `technical_synthesizer` — Creates detailed technical reports for professionals
- `consumer_synthesizer` — Creates accessible reports for general consumers

**Mock Tools**:
- `search_product_specifications()` — Returns realistic product specs
- `search_user_reviews()` — Returns mock user review data

## High-level flow

1) LLM router analyzes query → 2) Parallel worker agents execute with tools →
3) Gather results + detect audience → 4) Synthesis router decides → 
5) Generate final report (technical OR consumer format)

Each stage updates the shared `GraphState`, enabling full introspection and debugging.

## State schema

```python
class GraphState(BaseModel):
    query: str                          # User's research query
    routing_decision: List[str]         # Worker agents to invoke
    results: Dict[str, str]             # Worker outputs (merged)
    gathered_data: str                  # Combined worker data
    audience_type: str                  # "technical" or "consumer"
    synthesis_decision: str             # Which synthesizer to use
    final_report: str                   # Generated report
    messages: List[BaseMessage]         # Conversation history
```

## Example queries

```python
# Routes to both agents + technical synthesizer
"I need a comprehensive technical analysis of the iPhone 15 Pro for professional developers"

# Routes to reviews agent + consumer synthesizer  
"Should I buy the Samsung Galaxy S24 for everyday use? What do consumers think?"

# Routes to both agents + consumer synthesizer
"Give me a full product research report on the MacBook Pro M3 for general consumers"
```

## Customization

To add a new worker agent, update:
1. Create agent function with `@asyncflow` decorator and tools
2. Update router prompt to include new agent
3. Add node to graph: `graph.add_node("new_agent", agent_fn)`
4. Update `path_map` in router's conditional edges
5. Add edge to gather: `graph.add_edge("new_agent", "gather_results")`

To add a new synthesizer:
1. Create synthesizer function with `@asyncflow` decorator
2. Update audience detection logic in `gather_results()`
3. Update synthesis router logic
4. Add node and conditional edge paths
