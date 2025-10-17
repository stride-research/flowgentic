# Hierarchical Agent Pattern: Supervisor of Supervisors

This guide explains the hierarchical (multi-level supervisor) design pattern, an advanced extension of the supervisor pattern that enables scalable, multi-layered agent coordination.

## Understanding the Hierarchical Pattern

The **hierarchical pattern** extends the supervisor pattern by organizing agents into multiple levels of supervision. Instead of a single supervisor routing to all worker agents, you create a tree-like structure where high-level supervisors delegate to mid-level supervisors, which in turn manage specialized worker agents.

**Think of it like an organization:**
- **Top-level supervisor (CEO)**: Routes broad requests to department heads
- **Mid-level supervisors (Department heads)**: Route specialized tasks to their team members
- **Worker agents (Team members)**: Execute specific tasks with focused expertise

**Why use this pattern?**

- **Scalability**: Manage dozens or hundreds of agents without overwhelming a single router
- **Domain separation**: Organize agents by clear functional boundaries (research department, engineering department, etc.)
- **Reduced routing complexity**: Each supervisor only reasons about a small set of options
- **Improved accuracy**: Specialized supervisors have domain-specific routing logic
- **Flexible composition**: Mix sequential, parallel, and supervisor patterns at different levels
- **Cost efficiency**: Use cheaper models for lower-level routing decisions

**When to use it:**

- Large-scale multi-agent systems with many specialized agents
- Complex domains with clear hierarchical structure (e.g., enterprise workflows)
- Systems where different agent groups have distinct capabilities
- Scenarios requiring multi-stage delegation and specialization
- Applications where routing decisions need domain-specific expertise

---

## Conceptual Architecture

### Single-Level Supervisor (Review)

In the basic supervisor pattern, you have:

```
┌─────────────────┐
│   User Query    │
└────────┬────────┘
         │
    ┌────▼────┐
    │ Router  │ (Decides: agent_A, agent_B, or both)
    └────┬────┘
         │
    ┌────┴────┐
    │         │
┌───▼──┐  ┌──▼───┐
│Agent │  │Agent │
│  A   │  │  B   │
└───┬──┘  └──┬───┘
    │         │
    └────┬────┘
         │
    ┌────▼────┐
    │ Gather  │
    └─────────┘
```

**Limitations of single-level:**
- Router must understand ALL available agents
- Routing logic becomes complex with many agents
- All agents exist at the same logical "level"
- Hard to organize agents by domain or function

### Hierarchical Supervisor Architecture

In the hierarchical pattern, you nest supervisors within supervisors:

```
┌─────────────────────┐
│     User Query      │
└──────────┬──────────┘
           │
    ┌──────▼──────┐
    │ Top-Level   │ (Decides: Research, Engineering, or Sales)
    │  Supervisor │
    └──────┬──────┘
           │
    ┌──────┴──────────────────────┐
    │                             │
┌───▼────────┐          ┌────────▼─────┐
│ Research   │          │ Engineering  │
│ Supervisor │          │  Supervisor  │
└───┬────────┘          └────────┬─────┘
    │                            │
    │ Decides:                   │ Decides:
    │ - Web Search              │ - Code Generation
    │ - Paper Analysis          │ - Code Review
    │ - Data Collection         │ - Testing
    │                           │
┌───┴─────┐              ┌─────┴────┐
│         │              │          │
▼         ▼              ▼          ▼
[Worker   [Worker        [Worker    [Worker
Agents]   Agents]        Agents]    Agents]
│         │              │          │
└────┬────┘              └─────┬────┘
     │                         │
     │                         │
     └──────────┬──────────────┘
                │
         ┌──────▼──────┐
         │  Top-Level  │
         │   Gather    │
         └─────────────┘
```

**Benefits of hierarchy:**
- Top supervisor only reasons about 3-4 departments (not 20+ agents)
- Each mid-level supervisor is an expert in its domain
- Clear organizational structure mirrors real-world team dynamics
- Easy to add new agents to a specific department
- Routing prompts are simpler at each level

---

## Key Architectural Concepts

### 1. Supervisor Layers

A hierarchical system typically has 2-3 layers:

**Layer 1: Top-level supervisor**
- Receives the original user query
- Routes to high-level functional areas (departments)
- Uses a powerful model for broad decision-making
- Example routing: "This is a research task" → research department

**Layer 2: Department supervisors**
- Receive delegated tasks from top-level supervisor
- Route to specialized worker agents within their domain
- Use domain-specific routing logic
- Can use cheaper models since routing space is smaller
- Example routing: "This needs web search + data extraction" → both agents

**Layer 3: Worker agents**
- Execute specific tasks with focused tools
- Return results to their department supervisor
- Can be simple functions or complex ReAct agents
- Example: Web search agent uses search API and extracts relevant data

### 2. State Propagation

State flows through the hierarchy differently than in flat patterns:

**Downward flow (delegation):**
```python
# Top-level state
class TopLevelState:
    user_query: str
    top_routing_decision: Optional[str]  # "research" or "engineering"
    department_results: Dict[str, Any]
    final_output: str

# Mid-level state (research department)
class ResearchDepartmentState:
    delegated_task: str  # Extracted from top-level query
    department_routing_decision: List[str]  # ["web_search", "paper_analysis"]
    agent_results: Dict[str, str]
    department_summary: str
```

**Upward flow (result aggregation):**
- Worker agents return results to department supervisor
- Department supervisor synthesizes results into a department summary
- Department summaries flow back to top-level gather node
- Top-level gather synthesizes final output

**Key insight:** Each level has its own state schema, but they're related through transformation:
- Top-level state → Extract relevant portion → Mid-level state
- Mid-level results → Summarize → Top-level state update

### 3. Routing Prompt Hierarchy

Each supervisor level has its own routing prompt that reflects its scope:

**Top-level routing prompt:**
```
Based on the user's query, decide which department should handle it:

- research_department: Handles information gathering, data analysis, 
  literature review, and web research tasks
- engineering_department: Handles code generation, debugging, testing, 
  and technical implementation tasks
- sales_department: Handles customer communication, proposal generation, 
  and business strategy tasks

User query: "{query}"

Respond with the department name: ["research_department"]
For multiple departments: ["research_department", "engineering_department"]
```

**Department-level routing prompt (Research):**
```
Based on the delegated research task, decide which specialized agents to use:

- web_search_agent: Searches the internet for current information
- paper_analysis_agent: Analyzes academic papers and technical documentation
- data_collection_agent: Gathers and structures data from various sources
- summarization_agent: Creates summaries from large documents

Delegated task: "{delegated_task}"

Respond with agent names: ["web_search_agent", "paper_analysis_agent"]
```

**Design principle:** Each routing prompt only describes options at that level, keeping decisions manageable.

### 4. Parallel Execution Across Levels

The hierarchical pattern enables parallelism at multiple levels:

**Horizontal parallelism (within a department):**
- Department supervisor routes to multiple worker agents
- Agents execute in parallel (same as basic supervisor pattern)
- Example: Web search agent and paper analysis agent run simultaneously

**Vertical parallelism (across departments):**
- Top-level supervisor routes to multiple departments
- Different departments execute in parallel
- Each department internally runs its own parallel agents
- Example: Research department and engineering department work simultaneously

**Combined parallelism example:**
```
Top supervisor routes to [Research, Engineering]
├─ Research department routes to [Web Search, Paper Analysis]
│  ├─ Web Search executes (3s)
│  └─ Paper Analysis executes (3s)
│  └─ Research gather synthesizes (1s) → Total: 4s
│
└─ Engineering department routes to [Code Gen, Testing]
   ├─ Code Gen executes (5s)
   └─ Testing executes (2s)
   └─ Engineering gather synthesizes (1s) → Total: 6s

Top-level gather synthesizes both departments (1s)
Total wall time: ~7s (instead of 15s sequentially)
```

### 5. Gather Nodes at Each Level

Each supervisor level needs its own gather node:

**Department-level gather:**
- Collects results from worker agents within the department
- Synthesizes into a coherent department summary
- Returns summary to top-level state
- Example: "Research department found 5 relevant papers and 10 web sources supporting the hypothesis..."

**Top-level gather:**
- Collects summaries from all departments
- Synthesizes into final user-facing output
- Integrates perspectives from different domains
- Example: "Based on research findings and engineering feasibility analysis, the recommendation is..."

**Design tip:** Use different LLM models for different gather levels:
- Department gather: Use fast, cheap models (Gemini Flash)
- Top-level gather: Use high-quality models (Claude Sonnet) for final output

---

## Implementation Strategy

### Step 1: Design the Hierarchy

Before coding, map out your agent hierarchy:

1. **Identify top-level domains** (research, engineering, sales, support, etc.)
2. **Define worker agents** for each domain
3. **Determine routing criteria** at each level
4. **Plan state schemas** for each level

**Example hierarchy:**
```
Top Level
├─ Research Department
│  ├─ Web Search Agent
│  ├─ Paper Analysis Agent
│  └─ Data Collection Agent
├─ Engineering Department
│  ├─ Code Generation Agent
│  ├─ Code Review Agent
│  └─ Testing Agent
└─ Sales Department
   ├─ Customer Research Agent
   ├─ Proposal Generation Agent
   └─ Pricing Analysis Agent
```

### Step 2: Define State Schemas for Each Level

Create separate state schemas that reflect the data flow:

**Top-level state:**
```python
class TopLevelState:
    user_query: str
    top_routing_decision: Optional[List[str]]
    research_summary: Optional[str]
    engineering_summary: Optional[str]
    sales_summary: Optional[str]
    final_output: str
    messages: Annotated[List[BaseMessage], add_messages]
```

**Department state (example for research):**
```python
class ResearchDepartmentState:
    delegated_query: str
    routing_decision: Optional[List[str]]
    agent_results: Annotated[Dict[str, str], operator.or_]
    department_summary: str
    messages: Annotated[List[BaseMessage], add_messages]
```

**Design principle:** Each state schema is self-contained for its level but includes fields to pass results upward.

### Step 3: Build Department Subgraphs

Each department is essentially a complete supervisor workflow:

**Conceptual structure:**
```python
def build_research_department_graph():
    """Build the research department subgraph."""
    graph = StateGraph(ResearchDepartmentState)
    
    # Create department-level router
    research_router = create_llm_router(research_routing_prompt, model)
    
    # Add worker agents
    graph.add_node("router", research_router)
    graph.add_node("web_search_agent", web_search_agent)
    graph.add_node("paper_analysis_agent", paper_analysis_agent)
    graph.add_node("gather", research_gather)
    
    # Connect with supervisor fan-out pattern
    graph.add_edge(START, "router")
    graph.add_conditional_edges(
        "router", 
        supervisor_fan_out, 
        path_map=["web_search_agent", "paper_analysis_agent"]
    )
    graph.add_edge("web_search_agent", "gather")
    graph.add_edge("paper_analysis_agent", "gather")
    graph.add_edge("gather", END)
    
    return graph.compile()
```

**Key insight:** Each department is a standalone supervisor graph that can be tested independently.

### Step 4: Create State Transformation Nodes

You need nodes that transform state between levels:

**Downward transformation (top → department):**
```python
def extract_research_task(top_state: TopLevelState) -> ResearchDepartmentState:
    """Transform top-level state into research department state."""
    return ResearchDepartmentState(
        delegated_query=top_state.user_query,
        # Extract relevant context for research department
    )
```

**Upward transformation (department → top):**
```python
def integrate_research_results(
    top_state: TopLevelState, 
    dept_state: ResearchDepartmentState
) -> TopLevelState:
    """Integrate research department results into top-level state."""
    top_state.research_summary = dept_state.department_summary
    return top_state
```

### Step 5: Assemble the Top-Level Graph

Connect everything at the top level using LangGraph's subgraph capabilities:

**Conceptual structure:**
```python
def build_hierarchical_graph():
    """Build the complete hierarchical graph."""
    graph = StateGraph(TopLevelState)
    
    # Top-level router
    top_router = create_llm_router(top_routing_prompt, powerful_model)
    
    # Department subgraphs (wrapped as nodes)
    research_dept = build_research_department_graph()
    engineering_dept = build_engineering_department_graph()
    
    # Add nodes
    graph.add_node("top_router", top_router)
    graph.add_node("research_department", research_dept)
    graph.add_node("engineering_department", engineering_dept)
    graph.add_node("top_gather", top_level_gather)
    
    # Connect with conditional routing
    graph.add_edge(START, "top_router")
    graph.add_conditional_edges(
        "top_router", 
        top_supervisor_fan_out, 
        path_map=["research_department", "engineering_department"]
    )
    graph.add_edge("research_department", "top_gather")
    graph.add_edge("engineering_department", "top_gather")
    graph.add_edge("top_gather", END)
    
    return graph.compile()
```

### Step 6: Implement Cross-Level Communication

Enable state passing between levels:

**Option 1: Shared state fields**
- Use common field names across state schemas
- LangGraph automatically propagates matching fields
- Simpler but less type-safe

**Option 2: Explicit transformation nodes**
- Add nodes that explicitly transform state between levels
- More verbose but type-safe and clear
- Recommended for production systems

**Option 3: State channels**
- Use LangGraph's state channels for cross-level communication
- Most flexible but more complex

---

## Advanced Patterns and Optimizations

### Dynamic Depth

Adapt hierarchy depth based on query complexity:

**Simple queries:** Bypass mid-level supervisors, route directly to workers
**Complex queries:** Use full 3-level hierarchy
**Implementation:** Top router includes "simple_task" option that routes to a single agent

### Caching and Memoization

Cache routing decisions and agent results:

- **Routing cache:** If the same query type appears, reuse routing decision
- **Result cache:** If a worker agent has processed similar input, return cached result
- **Benefit:** Dramatically reduces cost and latency for repeated patterns

### Model Selection by Level

Use different models at different levels:

- **Top-level router:** Use premium model (Claude Opus, GPT-4) for critical routing decisions
- **Department routers:** Use mid-tier models (Claude Sonnet, GPT-4-turbo)
- **Worker agents:** Use fast, cheap models (Gemini Flash, GPT-3.5) for execution
- **Gather nodes:** Match model to importance (top-level: premium, department: mid-tier)

**Cost/quality tradeoff:** This can reduce costs by 60-80% while maintaining high-quality final outputs.

### Error Handling and Fallbacks

Implement graceful degradation:

**Department-level failure:**
- If all agents in a department fail, return partial results or error state
- Top-level gather should handle missing department summaries

**Agent-level failure:**
- If one agent fails, department gather synthesizes from available results
- Consider fallback agents (e.g., if web search fails, try a different search agent)

**Routing failure:**
- If router can't decide, use a default routing strategy
- Log uncertainty and flag for human review

### Monitoring and Introspection

Track execution across the hierarchy:

**Per-level metrics:**
- Routing decision accuracy at each level
- Execution time per department
- Cost per department
- Agent utilization (which agents are called most often)

**Cross-level metrics:**
- End-to-end latency
- Parallelism efficiency (actual speedup vs. theoretical maximum)
- Cost per query
- Quality metrics (user satisfaction, error rates)

---

## Use Case Examples

### Enterprise Knowledge Management System

**Hierarchy:**
- **Top:** Legal, HR, Engineering, Finance
- **Legal:** Contract analysis, compliance checking, risk assessment
- **HR:** Policy lookup, benefits Q&A, onboarding guidance
- **Engineering:** Code search, documentation, architecture Q&A
- **Finance:** Budget queries, expense policies, financial reports

**Example query:** "What are the tax implications of our remote work policy?"
- Top router → Legal + HR + Finance
- Legal analyzes tax law compliance
- HR reviews remote work policy details
- Finance assesses tax documentation requirements
- Top gather synthesizes comprehensive answer

### Scientific Research Assistant

**Hierarchy:**
- **Top:** Literature review, Data analysis, Experimental design
- **Literature:** Paper search, citation analysis, summary generation
- **Data analysis:** Statistical analysis, visualization, pattern detection
- **Experimental design:** Protocol generation, controls, methodology

**Example query:** "Design an experiment to test the effect of temperature on enzyme activity"
- Top router → Literature + Experimental design
- Literature finds relevant prior studies on temperature effects
- Experimental design creates protocol with controls
- Top gather produces complete experimental plan with literature support

### Customer Support System

**Hierarchy:**
- **Top:** Technical support, Billing, Product info, Account management
- **Technical:** Troubleshooting, bug reports, feature requests
- **Billing:** Invoice queries, payment issues, refunds
- **Product:** Feature explanations, compatibility, recommendations

**Example query:** "My subscription charged me twice and now the app won't open"
- Top router → Billing + Technical support
- Billing investigates duplicate charge, initiates refund
- Technical diagnoses app issue (possibly related to account state from billing problem)
- Top gather provides coordinated response addressing both issues

---

## Trade-offs and Considerations

### When Hierarchical is Overkill

**Don't use hierarchical if:**
- You have fewer than 8-10 total agents
- All agents share similar capabilities (no clear domain boundaries)
- Routing logic is simple enough for a single prompt
- Latency is critical (hierarchical adds overhead from multiple routing steps)
- Budget is very tight (multiple LLM calls for routing add cost)

**Simpler alternatives:**
- Basic supervisor pattern for 3-8 agents
- Sequential pattern if tasks have clear ordering
- Direct agent selection if routing is deterministic

### Complexity Management

**Challenges:**
- More moving parts = more potential failure points
- Debugging is harder (errors could be at any level)
- State management is more complex
- Testing requires comprehensive scenarios

**Mitigation strategies:**
- Comprehensive logging at each level
- Unit test each department subgraph independently
- Use strong typing (Pydantic schemas) to catch state mismatches
- Start simple (2 levels) before adding more depth
- Build monitoring dashboards to visualize execution

### Performance Characteristics

**Latency:**
- Hierarchical adds routing overhead at each level
- Typical overhead: 200-500ms per routing decision
- Offset by parallelism gains when multiple departments/agents execute
- Net benefit when ≥2 departments or ≥3 agents run in parallel

**Cost:**
- More LLM calls for routing (top-level + department-level)
- Offset by using cheaper models for department routing
- Total cost typically 20-40% higher than flat supervisor for same task
- Worth it for significantly improved routing accuracy

**Scalability:**
- Scales much better than flat supervisor (linear vs. exponential routing complexity)
- Can handle hundreds of agents with manageable routing prompt size
- Essential for enterprise-scale multi-agent systems

---

## Antipatterns

### ❌ Over-Nesting: Too Many Hierarchical Levels

**Problem:** Creating 4+ levels of supervisors when 2-3 would suffice.

**Why it's bad:**
- Exponentially increases latency (each level adds routing overhead)
- Dramatically increases cost (more LLM calls for routing)
- Makes debugging nearly impossible (errors buried deep in hierarchy)
- Diminishing returns on routing accuracy after 3 levels

**Solution:** Limit to 2-3 levels maximum. If you need more, reconsider your domain boundaries.

```python
# ❌ BAD: Too many levels
Top → Department → Team → Sub-Team → Individual Agent
# 4 routing decisions before any work happens!

# ✅ GOOD: 2-3 levels maximum
Top → Department → Worker Agent
# or
Top → Department → Sub-Department → Worker Agent
```

### ❌ Using Hierarchical When Flat Supervisor Would Work

**Problem:** Implementing hierarchy for fewer than 8-10 total agents.

**Why it's bad:**
- Unnecessary complexity
- Added latency from multi-level routing
- Higher costs with no benefit
- Harder to maintain and debug

**Solution:** Use a flat supervisor pattern until you have enough agents to justify hierarchy.

```python
# ❌ BAD: Hierarchical for 5 agents
Top Supervisor
├─ Department A (2 agents)
└─ Department B (3 agents)

# ✅ GOOD: Flat supervisor for 5 agents
Supervisor → [Agent1, Agent2, Agent3, Agent4, Agent5]
```

### ❌ Vague Domain Boundaries Between Departments

**Problem:** Department responsibilities overlap or are poorly defined.

**Why it's bad:**
- Top-level router can't make clear decisions
- Same query could route to multiple departments with conflicting results
- Unclear which department owns which functionality
- Wasted parallel execution when departments duplicate work

**Solution:** Define clear, mutually exclusive domain boundaries.

```python
# ❌ BAD: Overlapping domains
research_department: "Handles information gathering and analysis"
analysis_department: "Handles data analysis and insights"
# ^ These overlap! What's the difference?

# ✅ GOOD: Clear boundaries
research_department: "External information gathering (web search, papers, APIs)"
analysis_department: "Internal data processing (statistics, ML, visualization)"
# ^ Clear distinction between external vs. internal data
```

### ❌ Not Optimizing Model Selection by Level

**Problem:** Using the same expensive model for all routing and execution.

**Why it's bad:**
- Routing is simpler than execution, doesn't need expensive models
- Department-level routing is even simpler (smaller option space)
- Costs 3-5x more than necessary
- No performance benefit for routing decisions

**Solution:** Use tiered model selection based on decision complexity.

```python
# ❌ BAD: Same model everywhere
top_router = ChatLLMProvider(model="claude-opus-3")  # $15/1M tokens
dept_router = ChatLLMProvider(model="claude-opus-3")  # $15/1M tokens
worker_agents = ChatLLMProvider(model="claude-opus-3")  # $15/1M tokens

# ✅ GOOD: Tiered model selection
top_router = ChatLLMProvider(model="claude-sonnet-3.5")  # $3/1M tokens (complex routing)
dept_router = ChatLLMProvider(model="gemini-flash")  # $0.075/1M tokens (simple routing)
worker_agents = ChatLLMProvider(model="claude-opus-3")  # $15/1M tokens (quality work)
# Saves 60-80% on costs while maintaining quality
```

### ❌ Weak Routing Prompts at Each Level

**Problem:** Generic, vague routing prompts that don't guide the LLM effectively.

**Why it's bad:**
- Poor routing accuracy leads to wrong departments handling tasks
- LLM wastes tokens on uncertainty
- Users get irrelevant results
- Requires human intervention to fix routing mistakes

**Solution:** Write specific, example-rich routing prompts at each level.

```python
# ❌ BAD: Vague top-level prompt
"Route this query to the right department: {query}"

# ✅ GOOD: Specific, example-rich prompt
"""
Analyze the user's query and route to the appropriate department(s):

RESEARCH DEPARTMENT - Choose when query involves:
- Gathering external information (web search, academic papers, APIs)
- Market research, competitor analysis, trend identification
- Examples: "Find recent papers on AI safety", "What are competitors doing?"

ENGINEERING DEPARTMENT - Choose when query involves:
- Code generation, debugging, testing, or review
- Technical implementation, architecture design, system analysis
- Examples: "Build a REST API", "Debug this Python code", "Review architecture"

SALES DEPARTMENT - Choose when query involves:
- Customer communication, proposals, pricing, or strategy
- Sales collateral, pitch decks, ROI calculations
- Examples: "Draft a proposal for Client X", "What's the pricing for Enterprise?"

Query: {query}

Return: ["department_name"] or ["dept1", "dept2"] for multiple departments
"""
```

### ❌ No State Transformation Between Levels

**Problem:** Passing the entire top-level state down to departments unchanged.

**Why it's bad:**
- Department-level agents receive irrelevant data
- Increased token usage (LLM processes unnecessary context)
- Confuses department routing logic
- Violates separation of concerns

**Solution:** Transform state explicitly when crossing levels.

```python
# ❌ BAD: Pass entire state to department
def research_department_node(top_state: TopLevelState):
    return research_dept_graph.invoke(top_state)  # Too much data!

# ✅ GOOD: Transform state for department
def research_department_node(top_state: TopLevelState):
    # Extract only relevant data for research department
    dept_state = ResearchDepartmentState(
        delegated_query=top_state.user_query,
        context=top_state.research_context,  # Only relevant fields
        # Exclude sales_data, engineering_specs, etc.
    )
    result = research_dept_graph.invoke(dept_state)
    
    # Transform result back to top-level state
    top_state.research_summary = result.department_summary
    return top_state
```

### ❌ Ignoring Parallelism Across Departments

**Problem:** Running departments sequentially even when they're independent.

**Why it's bad:**
- Wasted time—departments could run simultaneously
- Poor user experience (slow responses)
- Underutilizes HPC infrastructure
- Defeats a key benefit of hierarchical pattern

**Solution:** Use conditional fan-out at top level to enable parallel department execution.

```python
# ❌ BAD: Sequential department execution
graph.add_edge("top_router", "research_department")
graph.add_edge("research_department", "engineering_department")
graph.add_edge("engineering_department", "top_gather")
# ^ Takes 10s if each department takes 5s

# ✅ GOOD: Parallel department execution
graph.add_conditional_edges(
    "top_router",
    top_supervisor_fan_out,  # Routes to multiple departments in parallel
    path_map=["research_department", "engineering_department"]
)
graph.add_edge("research_department", "top_gather")
graph.add_edge("engineering_department", "top_gather")
# ^ Takes 5s if departments run in parallel
```

---

## Next Steps

To implement a hierarchical system:

1. **Start with supervisor pattern** — Master the basics before adding hierarchy
2. **Identify natural divisions** — Look for clear functional boundaries in your domain
3. **Build one department** — Implement a single department subgraph as a proof of concept
4. **Test independently** — Ensure each department works well in isolation
5. **Add top-level routing** — Connect departments with a top-level supervisor
6. **Iterate on routing prompts** — Refine routing logic based on real usage patterns
7. **Add monitoring** — Track execution patterns to optimize the hierarchy

Explore related patterns:
- [Supervisor Pattern](supervisor.md) — The foundation for hierarchical systems
- [Sequential Pattern](sequential.md) — Combine with hierarchical for sequential department workflows
- [Chatbot Pattern](chatbot.md) — Use hierarchical routing in conversational agents

---

## Conclusion

The hierarchical pattern is a powerful tool for building scalable, organized multi-agent systems. While it adds complexity, the benefits—improved routing accuracy, better organization, and horizontal scalability—make it essential for large-scale applications.

**Key takeaway:** Think of hierarchical agents like a well-organized company. Each level has clear responsibilities, delegates appropriately, and synthesizes results from its subordinates. This mirrors proven organizational patterns from the real world and brings those benefits to your AI agent architecture.
