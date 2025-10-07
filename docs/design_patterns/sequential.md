# Building a Sequential (Pipeline) Workflow with FlowGentic

This guide explains the sequential (pipeline) design pattern in multi-agent architectures and shows a concrete way to implement it with FlowGentic's LangGraph integration.

## Understanding the Sequential Pattern

The **sequential pattern** is a design where work progresses through well-defined stages in order. Each stage completes before the next begins, and the output of one stage becomes the input to the next. Think of it as an assembly line: raw materials → processing → assembly → quality check → packaging.

**Why use this pattern?**

- **Predictable flow**: Each stage has a clear contract—it knows what to expect as input and what to produce as output
- **Easier debugging**: When something fails, you know exactly which stage broke and can inspect the state at that point
- **Composable logic**: Stages can be developed, tested, and reasoned about independently
- **Controlled error handling**: Failures route to explicit error handlers rather than causing unpredictable cascades

**When to use it:**

- Multi-step research and analysis workflows
- Data pipelines with validation → transformation → enrichment → output
- Document generation with gather → synthesize → format → publish
- Any workflow where stages have clear dependencies and order matters

**What you'll build:**

A research-to-synthesis pipeline that takes a user query, validates it, runs a research agent with tools, prepares context for a synthesis agent, generates a deliverable, formats the output, and tracks everything for introspection.

---

## Step 1: Define the State Schema

**Concept:** In a sequential workflow, state is the data that flows from node to node. Think of it as a clipboard that gets passed down an assembly line—each worker reads it, updates it, and passes it along.

A well-designed state schema makes the workflow self-documenting: you can look at the state and immediately understand what data moves between stages, what each stage produces, and where failures might occur.

For this workflow, we need:
- **Input tracking**: the original user request
- **Validation results**: did preprocessing succeed?
- **Agent outputs**: what did the research and synthesis agents produce?
- **Context data**: enriched metadata passed between agents
- **Final output**: the finished deliverable
- **Error tracking**: a list of failures for debugging

Here's the implementation:

```python
from typing import Annotated, Optional, List, Dict, Any
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages


class ValidationData(BaseModel):
    """Captures preprocessing results."""
    is_valid: bool
    cleaned_input: str
    word_count: int
    timestamp: float
    metadata: Dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
    """Standardized output from any agent node."""
    agent_name: str
    output_content: str
    execution_time: float
    tools_used: List[str] = Field(default_factory=list)
    success: bool = True
    error_message: Optional[str] = None


class ContextData(BaseModel):
    """Context passed between stages."""
    previous_analysis: str
    input_metadata: ValidationData
    processing_stage: str
    agent_sequence: int
    additional_context: Dict[str, Any] = Field(default_factory=dict)


class WorkflowState(BaseModel):
    """The state that flows through the entire pipeline."""
    # Input
    user_input: str = ""

    # Preprocessing results
    validation_data: Optional[ValidationData] = None
    preprocessing_complete: bool = False

    # Agent execution results
    research_agent_output: Optional[AgentOutput] = None
    synthesis_agent_output: Optional[AgentOutput] = None
    messages: Annotated[List[BaseMessage], add_messages] = []

    # Context and intermediate data
    context: Optional[ContextData] = None

    # Final output
    final_output: str = ""
    workflow_complete: bool = False

    # Error handling
    errors: List[str] = Field(default_factory=list)
    current_stage: str = "initialized"
```

**Key points:**
- Pydantic gives you validation and type safety for free
- `Annotated[List[BaseMessage], add_messages]` uses LangGraph's reducer to merge message lists automatically
- Each field has a default, so nodes can partially update state without breaking downstream stages

---

## Step 2: Register Tools and Deterministic Tasks

**Concept:** In a sequential workflow, you'll have two types of operations:

1. **Agent tools**: functions the LLM can call (like web search or data analysis)
2. **Deterministic tasks**: pure functions that don't involve the LLM (like validation or formatting)

By organizing them separately, you make it clear which operations involve non-deterministic LLM reasoning and which are predictable transformations. This helps with testing, debugging, and cost estimation (LLM calls are expensive; deterministic tasks are cheap).

FlowGentic uses the `@asyncflow` decorator with different `flow_type` values to register each type. The registry pattern centralizes tool management so your nodes don't have to know where tools come from.

### Defining Agent Tools

Agent tools are callable by the LLM during reasoning. They need clear docstrings (the LLM uses them to decide when to call the tool) and structured return values.

```python
from flowgentic.langGraph.agents import AsyncFlowType


class ResearchTools:
    """Tools for the research agent to gather and analyze information."""
    
    def __init__(self, agents_manager):
        self.agents_manager = agents_manager

    def register_tools(self):
        @self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION)
        async def web_search_tool(query: str) -> str:
            """Search the web for information about a topic."""
            # In production: call a real search API
            return f"Search results for '{query}': Found relevant information..."

        @self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION)
        async def data_analysis_tool(data: str) -> Dict[str, Any]:
            """Analyze data and extract insights."""
            # In production: perform real analysis
            return {
                "insights": f"Analysis of '{data[:50]}...'",
                "confidence": 0.85,
                "key_points": ["Point 1", "Point 2"]
            }

        return {
            "web_search": web_search_tool,
            "data_analysis": data_analysis_tool
        }
```

### Defining Deterministic Tasks

Deterministic tasks are predictable operations that don't call the LLM. They're perfect for preprocessing, validation, and formatting.

```python
class ValidationTasks:
    """Deterministic validation and preprocessing operations."""
    
    def __init__(self, agents_manager):
        self.agents_manager = agents_manager

    def register_function_tasks(self):
        @self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.FUNCTION_TASK)
        async def validate_input_task(user_input: str) -> ValidationData:
            """Validate and preprocess user input."""
            return ValidationData(
                is_valid=len(user_input.strip()) > 0,
                cleaned_input=user_input.strip(),
                word_count=len(user_input.split()),
                timestamp=asyncio.get_event_loop().time(),
                metadata={"domain": "energy" if "energy" in user_input.lower() else "general"}
            )

        return {"validate_input": validate_input_task}
```

### Centralized Registry

The registry inherits from `BaseToolRegistry` and aggregates all tools and tasks. This makes it easy to see what operations are available and ensures everything is registered before the workflow runs.

```python
from flowgentic.langGraph.base_components import BaseToolRegistry


class ActionsRegistry(BaseToolRegistry):
    """Centralized registry of all workflow tools and tasks."""
    
    def __init__(self, agents_manager):
        super().__init__(agents_manager)
        # Initialize specialized managers
        self.research_tools = ResearchTools(agents_manager)
        self.synthesis_tools = SynthesisTools(agents_manager)
        self.validation_tasks = ValidationTasks(agents_manager)
        self.context_tasks = ContextTasks(agents_manager)
        self.formatting_tasks = FormattingTasks(agents_manager)

    def _register_agent_tools(self):
        """Aggregate all agent tools."""
        self.agent_tools.update(self.research_tools.register_tools())
        self.agent_tools.update(self.synthesis_tools.register_tools())

    def _register_function_tasks(self):
        """Aggregate all deterministic tasks."""
        self.deterministic_tasks.update(self.validation_tasks.register_function_tasks())
        self.deterministic_tasks.update(self.context_tasks.register_function_tasks())
        self.deterministic_tasks.update(self.formatting_tasks.register_function_tasks())
```

**Why this matters:**
- Clear separation of concerns: agent tools vs. deterministic tasks
- Easy to test: you can test tools and tasks independently
- Easy to extend: add a new tool class and register it
- Type safety: Pydantic schemas ensure tools return the right shape

---

## Step 3: Implement Workflow Nodes

**Concept:** Nodes are the stages in your pipeline. Each node is a function that:
1. Receives the current state
2. Performs some work (calling tools, invoking agents, transforming data)
3. Updates and returns the state

FlowGentic uses the `@asyncflow` decorator with `flow_type=AsyncFlowType.EXECUTION_BLOCK` to wrap nodes for HPC execution and introspection. By using the `@property` pattern, you can define nodes as methods that return closures—this gives each node access to `self.agents_manager` and `self.tools_registry` without polluting the function signature.

### Node Container Class

First, create a class that holds all nodes and provides a registry method:

```python
from flowgentic.langGraph.agents import AsyncFlowType


class WorkflowNodes:
    """Container for all workflow nodes."""
    
    def __init__(self, agents_manager, tools_registry):
        self.agents_manager = agents_manager
        self.tools_registry = tools_registry

    def get_all_nodes(self):
        """Return a dict of node_name -> callable for graph registration."""
        return {
            "preprocess": self.preprocess_node,
            "research_agent": self.research_agent_node,
            "context_preparation": self.context_preparation_node,
            "synthesis_agent": self.synthesis_agent_node,
            "finalize_output": self.finalize_output_node,
            "error_handler": self.error_handler_node,
        }
```

### Example Node: Preprocessing

The preprocessing node calls a deterministic validation task and updates the state with results. Notice how it:
- Retrieves a task from the registry
- Awaits the result (tasks run on the HPC backend)
- Updates multiple state fields
- Returns the modified state

```python
    @property
    def preprocess_node(self):
        @self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.EXECUTION_BLOCK)
        async def _preprocess_node(state: WorkflowState) -> WorkflowState:
            """Validate and preprocess the user input."""
            print("🔄 Preprocessing Node: Starting input validation...")
            
            # Retrieve the validation task from registry
            validate = self.tools_registry.get_function_task_by_name("validate_input")
            validation_data = await validate(state.user_input)
            
            # Update state with validation results
            state.validation_data = validation_data
            state.preprocessing_complete = validation_data.is_valid
            state.current_stage = "preprocessing_complete"
            
            print(f"✅ Preprocessing complete: {validation_data.word_count} words")
            return state

        return _preprocess_node
```

### Example Node: Research Agent

The research agent node creates a ReAct agent (LangGraph's agentic loop), gives it tools, and invokes it with a task. Notice:
- It pulls tools from the registry
- It uses `create_react_agent` (LangGraph's built-in pattern)
- It provides a system message to guide the agent's behavior
- It extends the state's message list with the agent's conversation

```python
from langgraph.prebuilt import create_react_agent
from flowgentic.utils.llm_providers import ChatLLMProvider
from langchain_core.messages import HumanMessage, SystemMessage


    @property
    def research_agent_node(self):
        @self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.EXECUTION_BLOCK)
        async def _research_agent_node(state: WorkflowState) -> WorkflowState:
            """Run the research agent with tools to gather information."""
            print("🔍 Research Agent Node: Starting research...")
            
            # Get tools from registry
            tools = [
                self.tools_registry.get_tool_by_name("web_search"),
                self.tools_registry.get_tool_by_name("data_analysis"),
            ]
            
            # Create a ReAct agent
            agent = create_react_agent(
                model=ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash"),
                tools=tools,
            )
            
            # Invoke the agent
            result = await agent.ainvoke({
                "messages": [
                    SystemMessage(content="You are a research agent specializing in technology analysis."),
                    HumanMessage(content=state.user_input),
                ]
            })
            
            # Update state with agent's messages
            state.messages.extend(result.get("messages", []))
            state.current_stage = "research_complete"
            
            print("✅ Research complete")
            return state

        return _research_agent_node
```

**Key insights:**
- Nodes are just async functions that take state and return state
- The `@asyncflow` decorator enables HPC execution and introspection
- The `@property` pattern gives nodes access to shared resources (tools, agents_manager)
- Each node updates `current_stage` to track progress

---

## Step 4: Conditional Edge Logic

**Concept:** In a sequential workflow, you need **conditional routing**: after a stage completes, decide which stage comes next based on the current state. For example:
- If validation succeeds → go to research agent
- If validation fails → go to error handler
- If synthesis succeeds → go to finalization
- If synthesis fails → go to error handler

LangGraph uses conditional edges for this. A conditional edge is a function that:
1. Receives the current state
2. Returns a string key (like `"research_agent"` or `"error_handler"`)
3. The graph uses that key to route to the next node

By encapsulating all routing logic in a dedicated `WorkflowEdges` class, you make the flow explicit and testable.

```python
class WorkflowEdges:
    """Encapsulates all conditional routing logic."""
    
    @staticmethod
    def should_continue_after_preprocessing(state: WorkflowState) -> str:
        """Route after preprocessing: success → research, failure → error."""
        if state.preprocessing_complete and state.validation_data.is_valid:
            return "research_agent"
        else:
            return "error_handler"

    @staticmethod
    def should_continue_after_research(state: WorkflowState) -> str:
        """Route after research: success → context prep, failure → error."""
        if state.research_agent_output and state.research_agent_output.success:
            return "context_preparation"
        else:
            return "error_handler"

    @staticmethod
    def should_continue_after_synthesis(state: WorkflowState) -> str:
        """Route after synthesis: success → finalize, failure → error."""
        if state.synthesis_agent_output and state.synthesis_agent_output.success:
            return "finalize_output"
        else:
            return "error_handler"
```

**Why this pattern:**
- **Explicit**: You can read the routing logic in one place
- **Testable**: Mock a state and verify the routing decision
- **Maintainable**: Adding new routes is straightforward
- **Self-documenting**: Each method name describes the decision point

---

## Step 5: Build the Graph

**Concept:** The graph builder assembles everything into a runnable workflow. It:
1. Registers all tools and tasks
2. Creates a `StateGraph` (LangGraph's workflow container)
3. Adds all nodes to the graph
4. Wraps each node with introspection (for telemetry)
5. Defines conditional edges (routing logic)
6. Sets the entry point (where the workflow starts)

This is the "wiring" step—you're connecting all the pieces you built in previous steps into a coherent pipeline.

```python
from langgraph.graph import StateGraph, END


class WorkflowBuilder:
    """Assembles the complete workflow graph."""
    
    def __init__(self, agents_manager):
        self.agents_manager = agents_manager
        self.tools_registry = ActionsRegistry(agents_manager)
        self.nodes = WorkflowNodes(agents_manager, self.tools_registry)
        self.edges = WorkflowEdges()

    def _register_nodes_to_introspector(self):
        """Tell the introspector about all nodes for telemetry."""
        all_node_names = list(self.nodes.get_all_nodes().keys())
        self.agents_manager.agent_introspector._all_nodes = all_node_names

    def build_workflow(self) -> StateGraph:
        """Build and return the complete workflow graph."""
        
        # Step 1: Register all tools and tasks
        self.tools_registry._register_toolkit()
        
        # Step 2: Create the state graph
        workflow = StateGraph(WorkflowState)
        
        # Step 3: Add all nodes with introspection
        for node_name, node_function in self.nodes.get_all_nodes().items():
            # Wrap node for telemetry tracking
            instrumented_node = self.agents_manager.agent_introspector.introspect_node(
                node_function, node_name=node_name
            )
            workflow.add_node(node_name, instrumented_node)
        
        # Step 4: Register nodes for final report generation
        self._register_nodes_to_introspector()
        
        # Step 5: Define conditional edges (routing logic)
        workflow.add_conditional_edges(
            "preprocess",
            self.edges.should_continue_after_preprocessing,
            {"research_agent": "research_agent", "error_handler": "error_handler"},
        )
        
        workflow.add_conditional_edges(
            "research_agent",
            self.edges.should_continue_after_research,
            {"context_preparation": "context_preparation", "error_handler": "error_handler"},
        )
        
        workflow.add_conditional_edges(
            "synthesis_agent",
            self.edges.should_continue_after_synthesis,
            {"finalize_output": "finalize_output", "error_handler": "error_handler"},
        )
        
        # Step 6: Add terminal edges
        workflow.add_edge("finalize_output", END)
        workflow.add_edge("error_handler", END)
        
        # Step 7: Set entry point
        workflow.set_entry_point("preprocess")
        
        return workflow
```

**What's happening:**
- `introspect_node` wraps each node to track execution time, calls, and errors
- `add_conditional_edges` maps return values from edge functions to next nodes
- `add_edge(..., END)` marks terminal nodes (where the workflow stops)
- `set_entry_point` defines where the workflow begins

---

## Step 6: Orchestrate and Run

**Concept:** This is where you bring everything together. You:
1. Initialize the HPC backend (ThreadPoolExecutor for concurrency)
2. Create the `LangraphIntegration` context manager (manages workflow lifecycle)
3. Build the workflow using your builder
4. Compile it with a checkpointer (for state persistence and memory)
5. Create an initial state
6. Stream execution (watch state evolve in real-time)
7. Generate an introspection report (see what happened)
8. Render the graph (visualize the workflow)

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from radical.asyncflow import ConcurrentExecutionBackend
from langgraph.checkpoint.memory import InMemorySaver

from flowgentic.langGraph.main import LangraphIntegration


async def start_app():
    # Initialize HPC backend
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())
    
    # Create the integration context
    async with LangraphIntegration(backend=backend) as agents_manager:
        # Build the workflow
        workflow_builder = WorkflowBuilder(agents_manager)
        workflow = workflow_builder.build_workflow()
        
        # Compile with memory
        app = workflow.compile(checkpointer=InMemorySaver())
        
        # Create initial state
        initial_state = WorkflowState(
            user_input=(
                "I need to research the latest developments in renewable energy storage "
                "technologies and create a comprehensive report with recommendations."
            )
        )
        
        # Stream execution
        config = {"configurable": {"thread_id": "1"}}
        async for chunk in app.astream(initial_state, config=config, stream_mode="values"):
            print(f"Chunk: {chunk}\n")
        
        # Generate telemetry report
        agents_manager.agent_introspector.generate_report()
        
        # Visualize the graph
        await agents_manager.utils.render_graph(app)


if __name__ == "__main__":
    asyncio.run(start_app())
```

**What streaming gives you:**
- Real-time visibility: see state updates as each node completes
- Progressive output: display results incrementally
- Debugging aid: spot failures as they happen, not at the end

**What introspection gives you:**
- Execution summary: which nodes ran, how long they took
- Tool usage: which tools were called and how often
- Cost tracking: estimate LLM token usage
- Debugging: trace failures to specific nodes

---

## Example Execution Output

When you run the workflow, you'll see streamed state updates as each node completes:

```text
🚀 Starting Sequential Agent Workflow
============================================
🔄 Preprocessing Node: Starting input validation...
✅ Preprocessing complete: 23 words

Chunk: {
  'current_stage': 'preprocessing_complete',
  'preprocessing_complete': True,
  'validation_data': ValidationData(is_valid=True, word_count=23, ...),
  ...
}

🔍 Research Agent Node: Starting research...
✅ Research complete

Chunk: {
  'current_stage': 'research_complete',
  'research_agent_output': AgentOutput(agent_name='Research Agent', ...),
  ...
}

🔧 Context Preparation Node: Preparing context...
✅ Context preparation complete

Chunk: {
  'current_stage': 'context_prepared',
  'context': ContextData(...),
  ...
}

🏗️ Synthesis Agent Node: Creating deliverables...
✅ Synthesis complete

Chunk: {
  'current_stage': 'synthesis_complete',
  'synthesis_agent_output': AgentOutput(agent_name='Synthesis Agent', ...),
  ...
}

📄 Finalize Output Node: Formatting final results...
✅ Final output formatting complete

Chunk: {
  'current_stage': 'completed',
  'workflow_complete': True,
  'final_output': '=== SEQUENTIAL WORKFLOW RESULTS ===\n...',
  ...
}
```

The introspection report will show:
- Total execution time
- Per-node execution time
- Tool invocation counts
- Success/failure status
- A visual graph of the workflow

---

## Complete Example Code

The full working example is available in the FlowGentic repository:

[**View Sequential Pattern Example →**](https://github.com/stride-research/flowgentic/blob/main/examples/langgraph-integration/design_patterns/sequential/main.py)

The example includes:
- `main.py` — Entry point and orchestration
- `components/builder.py` — Graph assembly
- `components/nodes.py` — All workflow nodes
- `components/edges.py` — Conditional routing logic
- `components/utils/actions.py` — Tools and tasks
- `components/utils/actions_registry.py` — Centralized registry
- `utils/schemas.py` — State and data models

---

## Troubleshooting

### Tools not available to agents

**Symptom:** Agent tries to call a tool but gets "tool not found" error.

**Solution:**
- Ensure `ActionsRegistry._register_toolkit()` is called in the builder before adding nodes
- Verify tools are decorated with `flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION`
- Check that the tool name in the registry matches the name you're retrieving

### Nodes not appearing in introspection report

**Symptom:** Report shows zero nodes or missing nodes.

**Solution:**
- Confirm nodes are wrapped via `agent_introspector.introspect_node()`
- Ensure `_register_nodes_to_introspector()` is called after adding all nodes
- Check that `agent_introspector._all_nodes` is set before running the workflow

### State not updating between nodes

**Symptom:** A node's changes to state don't appear in the next node.

**Solution:**
- Ensure each node returns the updated `state` object
- Verify you're using `state.field = value` (in-place mutation) or returning a new state
- Check that the state schema uses Pydantic correctly (fields must have types)

### Streaming produces no output

**Symptom:** `astream()` doesn't yield any chunks.

**Solution:**
- Pass a checkpointer to `workflow.compile(checkpointer=InMemorySaver())`
- Provide a stable `thread_id` in the config
- Use `stream_mode="values"` (not `"updates"` or `"messages"`)

### LLM calls fail or hang

**Symptom:** Agent nodes timeout or return errors.

**Solution:**
- Check that your `OPEN_ROUTER_API_KEY` (or other provider key) is set correctly
- Verify the model name is valid (e.g., `google/gemini-2.5-flash`)
- Try a simpler model for testing (flash models are faster and cheaper)
- Add retry logic or timeouts to agent invocations

### Graph doesn't render

**Symptom:** `render_graph()` throws an error or produces no output.

**Solution:**
- Ensure the workflow is compiled before rendering: `app = workflow.compile(...)`
- Check that graphviz is installed if using visual rendering
- Try rendering after the workflow completes (not during streaming)

---

## Next Steps

Now that you have a working sequential workflow:

- **Add more stages**: Insert new nodes for additional processing steps
- **Parallel execution**: Use FlowGentic's HPC features to run independent stages concurrently
- **Advanced memory**: Integrate long-term memory and summarization
- **Custom error handling**: Implement retry logic and fallback strategies
- **Production deployment**: Add logging, monitoring, and cost tracking

Explore other design patterns:
- [Chatbot Pattern](chatbot.md) — Interactive conversational agents
- [Supervisor Pattern](supervisor.md) — Supervisor agent coordination
- [Hierarchical Agent Pattern](hierachical.md) — Advanced supervisor design pattern
