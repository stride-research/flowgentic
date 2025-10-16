# Sales Proposal Generation - Supervisor Pattern Example

A sophisticated demonstration of the **Supervisor Design Pattern** for orchestrating multiple specialized agents to generate comprehensive sales proposals.

## 🎯 Overview

This example showcases the full capabilities of the supervisor pattern by coordinating a complex, multi-agent workflow for sales proposal generation. Unlike simpler sequential patterns, the supervisor dynamically routes work to specialized agents based on the task requirements.

## 🏗️ Architecture

### Agents & Responsibilities

#### 1. **Supervisor Agent** 🎯
- Orchestrates the entire proposal generation workflow
- Analyzes requirements and delegates tasks to appropriate specialists
- Coordinates the sequence: Data Analysis → Market Research → Document Creation
- Uses handoff tools to transfer work between agents

#### 2. **Data Analysis Agent** 📊
- Fetches customer history and relationship data
- Analyzes performance metrics and ROI
- Provides quantitative insights for the proposal

**Tools:**
- `fetch_customer_history` - Retrieves past purchases, interactions, and account status
- `analyze_performance_metrics` - Computes ROI, usage metrics, and growth indicators

#### 3. **Market Research Agent** 🔍
- Researches industry trends and forecasts
- Analyzes competitive landscape
- Identifies competitive advantages and market positioning

**Tools:**
- `research_industry_trends` - Gathers market intelligence and trend analysis
- `analyze_competitors` - Performs competitive analysis and identifies differentiators

#### 4. **Document Creation Agent** 📝
- Assembles the final proposal document
- Creates executive summary, technical sections, and pricing analysis
- Formats and structures the complete proposal

**Tools:**
- `create_executive_summary` - Generates high-level proposal overview
- `create_technical_section` - Builds technical capabilities and solutions
- `create_pricing_section` - Develops ROI analysis and pricing structure

## 🔄 Workflow

```
User Request
    ↓
Supervisor Agent (analyzes requirements)
    ↓
Data Analysis Agent (gathers customer data & metrics)
    ↓
Supervisor Agent (reviews data, delegates next task)
    ↓
Market Research Agent (analyzes market & competitors)
    ↓
Supervisor Agent (reviews insights, delegates final task)
    ↓
Document Creation Agent (assembles proposal)
    ↓
Supervisor Agent (confirms completion)
    ↓
Complete Proposal
```

## 🚀 Key Features

### 1. **Dynamic Task Routing**
The supervisor intelligently delegates work based on the current workflow stage, not a fixed sequence.

### 2. **Specialized Agent Tools**
Each agent has domain-specific tools that are wrapped in asyncflow execution blocks for HPC compatibility.

### 3. **State Management**
Custom `ProposalData` state tracks all gathered information throughout the workflow.

### 4. **Radical AsyncFlow Integration**
All tools and agent nodes execute through radical asyncflow:
- Tools: `AsyncFlowType.AGENT_TOOL_AS_FUNCTION`
- Agent Nodes: `AsyncFlowType.EXECUTION_BLOCK`

### 5. **Comprehensive Telemetry**
Full introspection and visualization of the agent execution workflow.

## 📋 Running the Example

### Prerequisites

```bash
# Ensure you have the required environment variables
export OPENROUTER_API_KEY="your-api-key"
```

### Execution

```bash
cd examples/langgraph-integration/design_patterns/supervisor/sales_proposal
python main.py
```

### Expected Output

```
🚀 Sales Proposal Generation - Supervisor Pattern
================================================================================

📋 Request:
Generate sales proposal for TechCorp Industries (Enterprise Software)
================================================================================

📊 Data Analysis Agent: Gathering customer data and metrics...
✅ Data Analysis Agent: Analysis complete

🔍 Market Research Agent: Analyzing market and competitors...
✅ Market Research Agent: Research complete

📝 Document Creation Agent: Assembling proposal document...
✅ Document Creation Agent: Proposal document complete

================================================================================
✅ Sales Proposal Generation Completed!

📄 The complete proposal has been assembled with:
   • Customer history and performance analysis
   • Industry trends and competitive insights
   • Executive summary, technical specs, and pricing

📊 Execution report and graph visualization generated!
```

## 🎓 Learning Objectives

### Supervisor Pattern Capabilities

1. **Multi-Agent Coordination**
   - Supervisor routes work to 3+ specialized agents
   - Each agent focuses on its domain expertise
   - Results flow back to supervisor for coordination

2. **Complex Workflows**
   - Sequential dependencies (data → research → document)
   - State accumulation across agent executions
   - Dynamic decision-making based on workflow progress

3. **Real-World Business Logic**
   - Simulates realistic sales proposal generation
   - Demonstrates practical agent specialization
   - Shows how to structure complex business processes

4. **HPC Integration**
   - All operations wrapped in radical asyncflow
   - Tools execute as async functions
   - Agent nodes run in execution blocks
   - Ready for high-performance computing environments

## 🔧 Customization

### Add New Agents

```python
# Create a new specialized agent
transfer_to_legal = agents_manager.execution_wrappers.create_task_description_handoff_tool(
    agent_name="legal_reviewer",
    description="Assign task to Legal Review Agent for compliance checks.",
)

@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.EXECUTION_BLOCK
)
async def legal_reviewer_node(state: WorkflowState) -> WorkflowState:
    # Agent implementation
    ...
```

### Add New Tools

```python
@agents_manager.execution_wrappers.asyncflow(
    flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
)
async def generate_case_studies(industry: str) -> str:
    """Generate relevant case studies for the proposal."""
    # Tool implementation
    ...
```

### Modify Workflow Logic

Update the supervisor's prompt to change delegation strategy:

```python
supervisor_agent = create_react_agent(
    model=ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash"),
    tools=[...],
    prompt="Your custom workflow strategy here...",
    name="supervisor",
)
```

## 📊 Output Artifacts

After execution, you'll find:

1. **Execution Report**: `agent_execution_results/execution_summary.md`
   - Agent execution timeline
   - Token usage statistics
   - Execution paths and decisions

2. **Graph Visualization**: `agent_execution_results/agent_graph.png`
   - Visual representation of the agent workflow
   - Node connections and flow paths

## 🆚 Comparison to Sequential Pattern

| Aspect | Sequential Pattern | Supervisor Pattern |
|--------|-------------------|-------------------|
| **Control Flow** | Fixed linear sequence | Dynamic routing by supervisor |
| **Agent Coordination** | Automatic progression | Explicit delegation |
| **Flexibility** | Rigid pipeline | Adaptable to task complexity |
| **Use Case** | Simple workflows | Complex orchestration |
| **Decision Making** | Pre-defined steps | Supervisor decides next step |

## 🔗 Related Examples

- **Simple Supervisor**: [`/supervisor/toy/`](../toy/) - Basic supervisor pattern
- **Sequential Pattern**: [`/sequential/research_agent/`](../../sequential/research_agent/) - Linear workflow
- **Financial Advisor**: [`/sequential/financial_advisor/`](../../sequential/financial_advisor/) - Sequential business logic


## 💡 Pro Tips

1. **Tool Design**: Keep tools focused and single-purpose for better agent reasoning
2. **State Management**: Use Pydantic models for type-safe state handling
3. **Error Handling**: Supervisor can implement retry logic for failed delegations
4. **Performance**: AsyncFlow enables parallel execution when agents don't have dependencies
5. **Scaling**: Add more specialized agents without changing the core workflow structure

---

**Built with**: LangGraph + Flowgentic + Radical AsyncFlow

