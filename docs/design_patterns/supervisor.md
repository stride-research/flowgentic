# Building a Supervisor Agent Workflow with FlowGentic

This guide explains the supervisor design pattern in multi-agent architectures and shows how to implement it with FlowGentic's LangGraph integration.

## Understanding the Supervisor Pattern

The **supervisor pattern** is a design where an intelligent router (the "supervisor") analyzes incoming tasks and dynamically dispatches them to specialized worker agents. Unlike sequential patterns where tasks flow through predetermined stages, the supervisor makes runtime decisions about which agents should handle each request—potentially routing to multiple agents in parallel.

**Why use this pattern?**

- **Dynamic routing**: LLM-based decisions adapt to varied inputs without hardcoded rules
- **Parallel execution**: Multiple specialized agents can work simultaneously on different aspects of a task
- **Specialization**: Each worker agent focuses on one domain (data processing, Q&A, analysis, etc.)
- **Scalability**: Easy to add new worker agents without restructuring the entire workflow
- **Intelligent synthesis**: Results from parallel agents can be combined into coherent outputs

**When to use it:**

- Tasks requiring multiple specialized capabilities (e.g., "analyze this data AND answer questions about it")
- Workloads where routing logic is complex or context-dependent
- Systems where different agents have distinct tools, models, or expertise
- Scenarios requiring parallel execution for performance

**What you'll build:**

A supervisor workflow with an LLM router that analyzes user queries and dispatches them to specialized agents (data processing, question answering, or both). The supervisor enables parallel execution and synthesizes results when multiple agents run.

[See the complete example code](https://github.com/stride-research/flowgentic/tree/main/examples/langgraph-integration/design_patterns/supervisor/toy)

**Important Note**
Currently the report generator for the supervisor and hierarchical pattern are partially functional. Please proceed to use them carefully while we working solving these issues.

---

## Implementation Steps Overview

Building a supervisor workflow involves these key steps:

1. **Define the graph state** by extending the default state structure to include routing decisions and agent results
2. **Create the LLM router** using the factory function with a routing prompt template
3. **Define specialized worker agents** with their specific behaviors and tools
4. **Add introspectable nodes** for telemetry and debugging (optional)
5. **Configure conditional edges** to fan out from router to workers based on LLM decisions
6. **Connect agents to a gather node** to synthesize parallel results

Let's implement each step in detail.

---

## Step 1: Define the Graph State

**Concept:** The supervisor pattern requires state that tracks routing decisions and collects results from multiple agents. FlowGentic provides utilities to make this easier, but you'll need to customize the state schema for your specific use case.

Key fields you'll need:
- **Input**: The user's query or request
- **Routing decision**: Which agents the LLM router selected
- **Agent results**: Outputs from each executed agent
- **Final summary**: Synthesized result (if multiple agents ran)
- **Messages**: Conversation history (required for LangGraph message passing)

Here's the implementation:

```python
from typing import Annotated, Dict, List, Optional
from pydantic import Field
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
import operator


class GraphState:
    """State schema for supervisor pattern workflow."""
    query: str = Field(..., description="User query to route")
    routing_decision: Optional[List[str]] = Field(
        default=None, 
        description="List of agents to route to"
    )
    results: Annotated[Dict[str, str], operator.or_] = Field(
        default_factory=dict, 
        description="Merge dicts from parallel agents"
    )
    final_summary: Optional[str] = Field(
        default=None, 
        description="Combined summary from gather node"
    )
    messages: Annotated[List[BaseMessage], add_messages] = []
```

**Key points:**
- `routing_decision` stores the LLM's decision (e.g., `["agent_A"]` or `["agent_A", "agent_B"]`)
- `results` uses `operator.or_` to merge dictionaries from parallel agents automatically
- `messages` uses `add_messages` reducer for conversation tracking
- `final_summary` holds the synthesized output when multiple agents run

---

## Step 2: Create the LLM Router

**Concept:** The LLM router is the "brain" of the supervisor pattern. It analyzes each incoming query and decides which specialized agents should handle it. FlowGentic provides a factory function `create_llm_router()` that builds this router for you.

The router needs:
1. A **routing prompt template** that explains the available agents and their capabilities
2. A **language model** to make routing decisions
3. Decoration with `@asyncflow` to enable HPC execution

### Define the Routing Prompt

The prompt should clearly describe each agent and when to use it:

```python
routing_prompt_template = """
Based on the user's query, decide which agent(s) should handle it:

- agent_A: Handles data processing and analysis tasks
- agent_B: Handles question answering and information retrieval
- both: When the query requires both processing AND answering

User query: "{query}"

Respond with a list of the agents to call: 
For example for agent_A: ["agent_A"]
For both: ["agent_A", "agent_B"]
"""
```

**Prompt design tips:**
- Be explicit about each agent's responsibilities
- Provide examples of the expected output format
- Include a "both" option for queries requiring multiple agents
- Use the `{query}` placeholder for dynamic insertion

### Create and Configure the Router

```python
from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.langGraph.utils.supervisor import create_llm_router
from flowgentic.utils.llm_providers import ChatLLMProvider

# Define the model for routing decisions
router_model = ChatLLMProvider(
    provider="OpenRouter", 
    model="google/gemini-2.5-flash"
)

# Create and decorate the router function using the factory
llm_router = agents_manager.execution_wrappers.asyncflow(
    create_llm_router(routing_prompt_template, router_model),
    flow_type=AsyncFlowType.EXECUTION_BLOCK
)
```

**What this does:**
- `create_llm_router()` returns an async function that handles routing logic
- The function formats the prompt with the actual query, calls the LLM, and parses the routing decision
- `@asyncflow` decoration enables execution on HPC backends and introspection tracking
- `AsyncFlowType.EXECUTION_BLOCK` marks this as a workflow execution node (not just a tool)

**Under the hood**, the router:
1. Takes the current state with the user query
2. Formats the routing prompt template
3. Invokes the LLM to get a routing decision
4. Parses and validates the decision as a list of agent names
5. Updates `state.routing_decision` and returns the updated state

---

## Step 3: Define Specialized Worker Agents

**Concept:** Worker agents are the specialists that handle actual work. Each agent focuses on a specific domain (data analysis, Q&A, web search, etc.). In the supervisor pattern, agents should:

- Be independently executable (no dependencies on other agents)
- Return standardized outputs that fit the `results` dictionary
- Update the state with their specific results
- Be decorated with `@asyncflow` for HPC execution

### Example Worker Agents

Here are two simple worker agents—one for data processing and one for question answering:

```python
import asyncio
import time
import logging


@agents_manager.execution_wrappers.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)
async def agent_a(state: GraphState) -> dict:
    """Data processing agent."""
    start = time.perf_counter()
    logging.info(f"📊 agent_a START - Processing data for: '{state.query}'")
    
    # Simulate data processing work
    await asyncio.sleep(3.0)
    
    elapsed = (time.perf_counter() - start) * 1000
    logging.info(f"📊 agent_a END   took_ms={elapsed:.1f}")
    
    return {"results": {"agent_A": f"Data analysis complete: {state.query}"}}


@agents_manager.execution_wrappers.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)
async def agent_b(state: GraphState) -> dict:
    """Question answering agent."""
    start = time.perf_counter()
    logging.info(f"💬 agent_b START - Answering: '{state.query}'")
    
    # Simulate Q&A work
    await asyncio.sleep(3.0)
    
    elapsed = (time.perf_counter() - start) * 1000
    logging.info(f"💬 agent_b END   took_ms={elapsed:.1f}")
    
    return {
        "results": {
            "agent_b": f"Answer: {state.query} - Parallelism is executing multiple tasks simultaneously!"
        }
    }
```

**Key design choices:**
- `flow_type=AsyncFlowType.FUNCTION_TASK` marks these as task functions (deterministic work)
- Each agent returns `{"results": {agent_name: result}}` for state merging
- Timing logs help track parallel execution performance
- Agents don't communicate directly—they only read/write state

### Advanced Worker Agents with Tools

For more complex scenarios, worker agents can use LangGraph's `create_react_agent` pattern:

```python
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import SystemMessage, HumanMessage


@agents_manager.execution_wrappers.asyncflow(flow_type=AsyncFlowType.EXECUTION_BLOCK)
async def research_agent(state: GraphState) -> dict:
    """Research agent with web search tools."""
    
    # Create a ReAct agent with specialized tools
    agent = create_react_agent(
        model=ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash"),
        tools=[web_search_tool, data_extraction_tool],
    )
    
    # Invoke the agent
    result = await agent.ainvoke({
        "messages": [
            SystemMessage(content="You are a research specialist. Use tools to gather information."),
            HumanMessage(content=state.query),
        ]
    })
    
    # Extract the final answer
    final_answer = result["messages"][-1].content
    
    return {"results": {"research_agent": final_answer}}
```

---

## Step 4: Create a Gather/Synthesis Node

**Concept:** When multiple agents run in parallel, you need a gather node to collect and synthesize their outputs into a coherent response. This node runs after all selected agents complete and combines their individual results.

```python
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.prebuilt import create_react_agent


@agents_manager.execution_wrappers.asyncflow(flow_type=AsyncFlowType.EXECUTION_BLOCK)
async def gather(state: GraphState) -> dict:
    """Gather and synthesize results from all executed agents using LLM."""
    logging.info(f"🔄 Gather: Combining results from agents...")
    
    results = state.results
    
    # If only one agent ran, just pass through
    if len(results) == 1:
        agent_name = list(results.keys())[0]
        final_summary = f"[Single Agent Result - {agent_name}]\n{results[agent_name]}"
        logging.info(f"✅ Gather: Single agent result passed through")
        return {"final_summary": final_summary}
    
    # If both agents ran, use LLM to synthesize their outputs
    synthesis_agent = create_react_agent(
        model=ChatLLMProvider(
            provider="OpenRouter", model="google/gemini-2.5-flash"
        ),
        tools=[],
    )
    
    synthesis_prompt = f"""
You are a synthesis agent. Combine the following outputs from two parallel agents into a coherent, comprehensive response.

Agent A (Data Processing): {results.get('agent_A', 'N/A')}

Agent B (Question Answering): {results.get('agent_B', 'N/A')}

Original Query: {state.query}

Create a brief, unified response that integrates both perspectives. Keep it concise (2-3 sentences).
"""
    
    synthesis_result = await synthesis_agent.ainvoke({
        "messages": [
            SystemMessage(content="You are a synthesis agent that combines multiple agent outputs into a coherent response."),
            HumanMessage(content=synthesis_prompt),
        ]
    })
    
    state.messages.extend(synthesis_result["messages"])
    
    final_summary = synthesis_result["messages"][-1].content.strip()
    
    logging.info(f"✅ Gather: Synthesized {len(results)} agent outputs")
    state.final_summary = final_summary
    return state
```

**Design decisions:**
- **Pass-through for single agents**: If only one agent ran, skip LLM synthesis (save cost/time)
- **LLM-based synthesis**: When multiple agents ran, use an LLM to intelligently combine outputs
- **Context preservation**: Include the original query in the synthesis prompt for coherence
- **Message tracking**: Extend `state.messages` to maintain conversation history

---

## Step 5: Add Introspection Nodes (Optional)

**Concept:** FlowGentic's introspection system tracks node execution, timing, and behavior for debugging and telemetry. Wrapping your nodes with `introspect_node()` enables automatic tracking without modifying node logic.

```python
# Wrap each node for introspection
llm_router_introspection = agents_manager.agent_introspector.introspect_node(
    llm_router, "llm_router"
)
agent_a_introspection = agents_manager.agent_introspector.introspect_node(
    agent_a, "agent_A"
)
agent_b_introspection = agents_manager.agent_introspector.introspect_node(
    agent_b, "agent_B"
)
gather_introspection = agents_manager.agent_introspector.introspect_node(
    gather, "gather"
)

# Register all nodes for report generation
agents_manager.agent_introspector._all_nodes = [
    "llm_router",
    "agent_A",
    "agent_B",
    "gather"
]
```

**Benefits:**
- Execution time tracking per node
- Call counts and patterns
- Error tracking and debugging
- Generate visual execution reports

---

## Step 6: Build the Supervisor Graph

**Concept:** Now we assemble all the pieces into a runnable workflow. The supervisor pattern has a specific structure:

1. **Router node** analyzes the query and decides which agents to invoke
2. **Conditional fan-out** routes to selected agents (potentially in parallel)
3. **Worker agents** execute their specialized tasks
4. **Gather node** synthesizes results from all executed agents

```python
from langgraph.graph import StateGraph, START, END
from flowgentic.langGraph.utils.supervisor import supervisor_fan_out


# Create the graph
graph = StateGraph(GraphState)

# Add nodes (using introspected versions)
graph.add_node("llm_router", llm_router_introspection)
graph.add_node("agent_A", agent_a_introspection)
graph.add_node("agent_B", agent_b_introspection)
graph.add_node("gather", gather_introspection)

# Define edges
graph.add_edge(START, "llm_router")

# Conditional fan-out: router decides which agents to invoke
# path_map specifies the possible agents that can be routed to
graph.add_conditional_edges(
    "llm_router", 
    supervisor_fan_out, 
    path_map=["agent_A", "agent_B"]
)

# All agents route to gather node
graph.add_edge("agent_A", "gather")
graph.add_edge("agent_B", "gather")

# Gather routes to end
graph.add_edge("gather", END)

# Compile the graph
app = graph.compile()
```

**Key points:**
- `supervisor_fan_out` is a built-in utility that reads `state.routing_decision` and creates `Send` commands for selected agents
- Conditional edges enable parallel execution—if the router selects both agents, they run simultaneously
- All agent paths converge at the gather node for synthesis
- The graph structure is explicit and self-documenting

### Understanding `supervisor_fan_out`

The `supervisor_fan_out` function (imported from `flowgentic.langGraph.utils.supervisor`) handles the fan-out logic:

```python
def supervisor_fan_out(state: GraphState) -> List[Send]:
    """Fan out based on LLM routing decision."""
    decision = state.routing_decision or []
    return [Send(agent, state) for agent in decision]
```

**How it works:**
1. Reads `state.routing_decision` (populated by the router)
2. Creates a `Send` command for each selected agent
3. LangGraph executes all `Send` commands in parallel

**Important:** When adding the conditional edge, you must specify `path_map` with the list of possible agent names:

```python
graph.add_conditional_edges(
    "llm_router", 
    supervisor_fan_out, 
    path_map=["agent_A", "agent_B"]
)
```

The `path_map` parameter tells LangGraph which nodes can be reached from this conditional edge. This is required for proper graph compilation and validation.

**Example routing scenarios:**
- Router decides `["agent_A"]` → Only agent_A runs
- Router decides `["agent_A", "agent_B"]` → Both agents run in parallel
- Router decides `[]` → No agents run (error case, should be handled)

---

## Step 7: Run the Workflow

**Concept:** Execute the supervisor workflow with test queries to see dynamic routing in action.

```python
import asyncio
import pathlib
import time
from concurrent.futures import ThreadPoolExecutor
from radical.asyncflow import ConcurrentExecutionBackend
from flowgentic.langGraph.main import LangraphIntegration


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(threadName)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Initialize HPC backend
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())

    async with LangraphIntegration(backend=backend) as agents_manager:
        # [Build graph as shown in previous steps]
        
        # Test different routing scenarios
        test_queries = [
            "Analyze the data and explain what parallelism means",
            "Just process this data please",
            "What is machine learning?",
        ]

        for query in test_queries:
            print("\n" + "="*80)
            logging.info(f"🚀 Testing query: '{query}'")
            print("="*80)
            
            wall_start = time.perf_counter()
            
            try:
                state = GraphState(query=query)
                result = await app.ainvoke(state)
                wall_ms = (time.perf_counter() - wall_start) * 1000
            except Exception as e:
                print(f"❌ Workflow execution failed: {str(e)}")
                raise
            finally:
                # Generate all execution artifacts (directories, report, graph)
                await agents_manager.generate_execution_artifacts(
                    app, __file__, final_state=result
                )

            print(f"\n📋 Results for: '{query}'")
            print(f"   Routing: {result['routing_decision']}")
            print(f"   Agent Outputs: {result['results']}")
            print(f"\n   💡 Final Summary:\n   {result.get('final_summary', 'N/A')}")
            logging.info(f"⏱️  WALL elapsed_ms={wall_ms:.1f}")


if __name__ == "__main__":
    asyncio.run(main(), debug=True)
```

**Key execution details:**
- Create the initial state with your query
- Use `ainvoke()` to execute the workflow
- In the `finally` block, call `generate_execution_artifacts()` to create output files

### Using the Execution Artifacts Facade

FlowGentic provides a convenient facade method `generate_execution_artifacts()` that handles all output generation in one call:

```python
await agents_manager.generate_execution_artifacts(
    app, __file__, final_state=result
)
```

**What it does:**
1. Creates the `agent_execution_results/` directory
2. Generates an execution summary markdown report
3. Renders a visual graph of your workflow

**Parameters:**
- `app`: The compiled StateGraph
- `__file__`: Python's built-in variable pointing to the current file (used to determine output directory)
- `final_state`: The final state after workflow execution (used for state introspection in reports)

**Generated artifacts:**
- `agent_execution_results/execution_summary.md` — Detailed execution report with timing, node calls, and state
- `agent_execution_results/agent_graph.png` — Visual representation of your workflow graph

This replaces the older pattern of calling three separate methods:
```python
# Old pattern (deprecated)
agents_manager.utils.create_output_results_dirs(current_directory)
agents_manager.agent_introspector.generate_report(dir_to_write=current_directory)
await agents_manager.utils.render_graph(app, dir_to_write=current_directory)
```

---

## Example Execution Output

When you run the workflow with a query that requires both agents, you'll see parallel execution:

```text
================================================================================
🚀 Testing query: 'Analyze the data and explain what parallelism means'
================================================================================
🧠 LLM Router: Analyzing query 'Analyze the data and explain what parallelism means'
✅ Router decided: ['agent_A', 'agent_B']

📊 agent_a START - Processing data for: 'Analyze the data and explain what parallelism means'
💬 agent_b START - Answering: 'Analyze the data and explain what parallelism means'

[Both agents run in parallel for 3 seconds]

📊 agent_a END   took_ms=3002.1
💬 agent_b END   took_ms=3001.8

🔄 Gather: Combining results from agents...
✅ Gather: Synthesized 2 agent outputs

📋 Results for: 'Analyze the data and explain what parallelism means'
   Routing: ['agent_A', 'agent_B']
   Agent Outputs: {
       'agent_A': 'Data analysis complete: Analyze the data and explain what parallelism means',
       'agent_b': 'Answer: Analyze the data and explain what parallelism means - Parallelism is executing multiple tasks simultaneously!'
   }

   💡 Final Summary:
   The data analysis confirms the query scope, while parallelism refers to executing multiple tasks at the same time, which is exactly what happened here—both agents processed different aspects of your request simultaneously, completing in ~3 seconds instead of ~6 seconds sequentially.

⏱️  WALL elapsed_ms=3847.2
```

**Key observations:**
- Both agents started nearly simultaneously
- Total wall time (~3.8s) is much less than sequential execution would be (~6s)
- The gather node synthesized outputs into a coherent final response
- Routing decision was intelligent and context-aware

---

## Complete Example Code

The full working example with all components is available here:

[**View Supervisor Pattern Example →**](https://github.com/stride-research/flowgentic/tree/main/examples/langgraph-integration/design_patterns/supervisor/toy)

The example includes:
- `main.py` — Complete workflow with router, agents, and gather node
- Logging and timing instrumentation
- Introspection integration
- Multiple test queries demonstrating different routing scenarios

---

## Antipatterns

### ❌ Poor Routing Prompt Design

**Problem:** Vague, generic routing prompts that don't clearly describe agent capabilities.

**Why it's bad:**
- LLM makes incorrect routing decisions
- Inconsistent behavior across similar queries
- Wasted parallel execution (wrong agents selected)
- Poor user experience with irrelevant results

**Solution:** Write detailed, example-rich routing prompts with clear agent descriptions.

```python
# ❌ BAD: Vague routing prompt
routing_prompt = """
Route this query to the right agent: {query}

Agents: agent_A, agent_B
"""

# ✅ GOOD: Detailed, example-rich prompt
routing_prompt = """
Based on the user's query, decide which agent(s) should handle it:

- agent_A: Handles DATA PROCESSING tasks including:
  * Statistical analysis and calculations
  * Data transformation and cleaning
  * Numerical computations
  * Examples: "Calculate average revenue", "Clean this dataset"

- agent_B: Handles QUESTION ANSWERING tasks including:
  * Information retrieval and explanation
  * Conceptual questions
  * How-to and why questions
  * Examples: "What is machine learning?", "Explain how APIs work"

- both: When the query requires BOTH processing AND explanation
  * Examples: "Analyze this data and explain the trends"

User query: "{query}"

Respond with: ["agent_A"] or ["agent_B"] or ["agent_A", "agent_B"]
"""
```

### ❌ Dependent Worker Agents

**Problem:** Creating worker agents that depend on each other's outputs.

**Why it's bad:**
- Defeats the purpose of parallel execution
- Creates hidden ordering dependencies
- Leads to race conditions or deadlocks
- Violates the supervisor pattern's independence principle

**Solution:** Keep workers independent; use the gather node to combine results.

```python
# ❌ BAD: Agent B depends on Agent A's output
async def agent_a(state: GraphState) -> dict:
    result = "Data analysis complete"
    return {"results": {"agent_A": result}}

async def agent_b(state: GraphState) -> dict:
    # This creates a dependency!
    agent_a_result = state.results.get("agent_A", "")
    # What if agent_A hasn't finished yet?
    return {"results": {"agent_B": f"Based on {agent_a_result}..."}}

# ✅ GOOD: Independent agents, gather combines
async def agent_a(state: GraphState) -> dict:
    result = "Data analysis complete"
    return {"results": {"agent_A": result}}

async def agent_b(state: GraphState) -> dict:
    # Independent work, doesn't need agent_A
    result = "Question answered independently"
    return {"results": {"agent_B": result}}

async def gather(state: GraphState) -> dict:
    # Gather combines results from both agents
    combined = f"{state.results['agent_A']} + {state.results['agent_B']}"
    return {"final_summary": combined}
```

### ❌ No Gather/Synthesis Strategy

**Problem:** Not combining results when multiple agents run in parallel.

**Why it's bad:**
- User receives fragmented, disconnected outputs
- No coherent narrative or conclusion
- Unclear how to interpret multiple agent results
- Wastes the value of parallel execution

**Solution:** Always implement a gather node that synthesizes parallel results.

```python
# ❌ BAD: No synthesis of parallel results
graph.add_edge("agent_A", END)
graph.add_edge("agent_B", END)
# ^ Results are separate, no coherent final output

# ✅ GOOD: Gather node synthesizes results
graph.add_edge("agent_A", "gather")
graph.add_edge("agent_B", "gather")
graph.add_edge("gather", END)

async def gather(state: GraphState) -> dict:
    # Check if multiple agents ran
    if len(state.results) > 1:
        # Use LLM to synthesize
        synthesis = await synthesize_results(state.results, state.query)
        return {"final_summary": synthesis}
    else:
        # Single agent, pass through
        return {"final_summary": list(state.results.values())[0]}
```

### ❌ Using Supervisor for Simple Sequential Flows

**Problem:** Implementing supervisor pattern when tasks have a clear sequential order.

**Why it's bad:**
- Unnecessary routing overhead
- LLM routing adds latency and cost
- Over-engineering a simple problem
- More potential points of failure

**Solution:** Use the Sequential pattern for workflows with clear stage dependencies.

```python
# ❌ BAD: Supervisor for sequential tasks
# Task is: validate → research → synthesize (always in this order)
routing_prompt = "Route to validate, research, or synthesize"
# ^ Why use LLM routing when order is fixed?

# ✅ GOOD: Sequential pattern for fixed order
workflow.add_edge("validate", "research")
workflow.add_edge("research", "synthesize")
workflow.add_edge("synthesize", END)
# ^ Clear, deterministic flow
```

### ❌ Not Using `path_map` in Conditional Edges

**Problem:** Forgetting to specify `path_map` when adding conditional edges.

**Why it's bad:**
- Graph compilation fails with cryptic errors
- LangGraph can't validate routing targets
- Runtime errors when routing to unknown nodes
- Difficult to debug

**Solution:** Always provide `path_map` with all possible routing targets.

```python
# ❌ BAD: Missing path_map
graph.add_conditional_edges(
    "llm_router",
    supervisor_fan_out
    # Missing path_map parameter!
)
# ^ Compilation error: "Cannot add edge to unknown node"

# ✅ GOOD: Explicit path_map
graph.add_conditional_edges(
    "llm_router",
    supervisor_fan_out,
    path_map=["agent_A", "agent_B"]  # All possible routing targets
)
```

### ❌ Incorrect State Merging Configuration

**Problem:** Not using proper reducers for state fields that receive multiple updates.

**Why it's bad:**
- Agent results overwrite each other instead of merging
- Lost data from parallel agents
- Unpredictable final state
- Silent data loss

**Solution:** Use appropriate reducers (`operator.or_` for dicts, `add_messages` for messages).

```python
# ❌ BAD: No reducer for results field
class GraphState(BaseModel):
    results: Dict[str, str] = {}  # No reducer!
    # ^ Second agent overwrites first agent's results

# ✅ GOOD: Use operator.or_ to merge dictionaries
import operator
from typing import Annotated

class GraphState(BaseModel):
    results: Annotated[Dict[str, str], operator.or_] = Field(default_factory=dict)
    # ^ Results from all agents are merged, not overwritten
```

### ❌ Overly Complex Routing Logic

**Problem:** Creating routing prompts with too many agents or complex conditional rules.

**Why it's bad:**
- LLM struggles to make accurate decisions with 10+ options
- Routing accuracy degrades exponentially with options
- Increased token usage for routing
- Consider hierarchical pattern instead

**Solution:** Limit flat supervisor to 3-8 agents; use hierarchical pattern for more.

```python
# ❌ BAD: Too many agents in one supervisor
routing_prompt = """
Route to one or more of these 15 agents:
- web_search_agent
- database_query_agent
- file_reader_agent
- email_sender_agent
- calendar_agent
- calculator_agent
- weather_agent
- news_agent
- translation_agent
- sentiment_analyzer
- code_generator
- testing_agent
- deployment_agent
- monitoring_agent
- alert_agent
...
"""
# ^ LLM routing accuracy drops significantly

# ✅ GOOD: Use hierarchical pattern
# Top-level supervisor routes to departments:
# - Data Department (web_search, database, file_reader)
# - Communication Department (email, calendar)
# - Engineering Department (code_gen, testing, deployment)
# Each department has its own supervisor for 3-5 agents
```

### ❌ Not Logging Router Decisions

**Problem:** No visibility into what the LLM router decided and why.

**Why it's bad:**
- Can't debug poor routing decisions
- No way to improve routing prompts
- Can't track routing patterns over time
- Difficult to optimize

**Solution:** Log routing decisions with context for analysis.

```python
# ✅ GOOD: Log routing decisions
async def llm_router_with_logging(state: GraphState) -> GraphState:
    decision = await router_llm.ainvoke(routing_prompt.format(query=state.query))
    
    # Log decision with context
    logging.info(
        f"🧠 Router Decision | Query: '{state.query}' | "
        f"Routed to: {decision} | Model: {router_llm.model}"
    )
    
    state.routing_decision = decision
    return state
```

---

## Troubleshooting

### Graph compilation fails with "unknown node" error

**Symptom:** Error like "Cannot add edge to unknown node" when compiling the graph.

**Solution:**
- Ensure you've added `path_map` parameter to `add_conditional_edges()`:
  ```python
  graph.add_conditional_edges(
      "llm_router", 
      supervisor_fan_out, 
      path_map=["agent_A", "agent_B"]  # ← Required!
  )
  ```
- Verify that all node names in `path_map` match the nodes you added to the graph
- Check that node names are strings and match exactly (case-sensitive)

### Router not selecting the right agents

**Symptom:** LLM router makes poor routing decisions or always routes to the same agent.

**Solution:**
- Improve the routing prompt with clearer agent descriptions
- Add more examples of input → routing decision pairs
- Try a more capable model (e.g., Claude instead of Gemini Flash)
- Log the router's reasoning to understand its decision process

### Agents not running in parallel

**Symptom:** Agents execute sequentially instead of simultaneously.

**Solution:**
- Verify `supervisor_fan_out` is used for conditional edges from the router
- Check that you're returning `Send` commands (not simple edge names)
- Ensure both agents have edges to the gather node (not chained to each other)
- Confirm HPC backend is properly initialized

### Gather node receives incomplete results

**Symptom:** `state.results` is missing data from one or more agents.

**Solution:**
- Verify each agent returns `{"results": {agent_name: output}}` format
- Check that `results` field uses `operator.or_` for dictionary merging
- Ensure agents don't overwrite each other's keys (use unique agent names)
- Add logging to agent return values

### State not merging correctly

**Symptom:** Agent results don't appear in the final state.

**Solution:**
- Confirm `Annotated[Dict[str, str], operator.or_]` is used for the `results` field
- Verify agents return dictionaries with the correct structure
- Check that `add_messages` reducer is used for the messages field
- Test state merging logic independently

### Introspection report shows missing nodes

**Symptom:** Some nodes don't appear in the generated report.

**Solution:**
- Ensure all nodes are wrapped with `agent_introspector.introspect_node()`
- Verify `_all_nodes` list includes every node name
- Check that node names match between graph definition and introspector registration
- Call `generate_report()` after workflow execution completes

---

## Advanced Example: Product Research Assistant

Once you've mastered the basic supervisor pattern, explore the **Product Research Assistant** example that demonstrates advanced concepts:

[**View Advanced Supervisor Example →**](https://github.com/stride-research/flowgentic/tree/main/examples/langgraph-integration/design_patterns/supervisor/product_research)

**Key innovations in this example:**

1. **Real LLM Worker Agents**: Uses `create_react_agent()` instead of simple functions
2. **Two-Stage Routing**: Initial routing to workers + conditional routing to synthesizers
3. **Context-Aware Synthesis**: Automatically detects audience type and selects appropriate synthesizer
4. **Dual Synthesizer Strategies**: Technical reports for professionals vs consumer reports for general buyers

**Workflow architecture:**
```
START → llm_router → [tech_agent || reviews_agent] → gather → synthesis_router 
      → [technical_synthesizer OR consumer_synthesizer] → END
```

**What you'll learn:**
- How to use actual LLM agents with domain-specific prompts
- Implementing conditional synthesis based on extracted context
- Building multi-stage routing architectures
- Combining parallel and conditional execution patterns

**Example queries:**
- "I need a comprehensive technical analysis of the iPhone 15 Pro for professional developers" 
  → Routes to both agents + technical synthesizer
- "Should I buy the Samsung Galaxy S24 for everyday use?"
  → Routes to reviews agent + consumer synthesizer

---

## Next Steps

Now that you have a working supervisor workflow:

- **Add more specialized agents**: Create agents for web search, data analysis, code generation, etc.
- **Improve routing intelligence**: Use few-shot examples in the routing prompt
- **Add error handling**: Implement fallback logic when agents fail
- **Optimize parallel execution**: Use HPC backends for truly distributed execution
- **Implement hierarchical supervision**: Create supervisors of supervisors (see [Hierarchical Pattern](hierachical.md))

Explore other design patterns:
- [Sequential/Pipeline Pattern](sequential.md) — Step-by-step workflows with clear dependencies
- [Chatbot Pattern](chatbot.md) — Interactive conversational agents
- [Hierarchical Agent Pattern](hierachical.md) — Advanced multi-level supervisor architecture
