# Toy Example: Supervisor + Conditional Edges + Send API

This is a minimal, throwaway example demonstrating **LLM-based supervisor routing with parallel agent execution** using LangGraph's Send API.

## Key Features Demonstrated

### 1. **Conditional Edges** 🧭
The supervisor agent makes routing decisions based on the user's request:
- **"analyze"** → Routes to parallel execution
- **"simple"** → Routes to single agent
- **Other** → Routes to END

### 2. **Send API for Parallelism** 🚀
When parallel route is chosen, the `send_to_parallel_agents()` function returns:
```python
[
    Send("data_agent", state),
    Send("research_agent", state),
    Send("processing_agent", state),
]
```
This triggers **simultaneous execution** of all three agents.

### 3. **Tangible Parallelism Validation** ⏱️
Each agent tool has a 2-second sleep to simulate work:
- **Sequential execution**: 3 agents × 2 seconds = **6 seconds**
- **Parallel execution**: All run simultaneously = **~2 seconds**

The example prints timestamps and calculates speedup to prove parallelism works.

## Architecture

```
START → supervisor (LLM makes decision)
         │
         ├─[conditional edge]
         │
         ├─→ parallel_route (Send API)
         │    ├→ data_agent ────┐
         │    ├→ research_agent ─┤
         │    └→ processing_agent┤
         │                        ├→ aggregator → END
         │
         ├─→ single_route
         │    └→ single_agent → END
         │
         └─→ end → END
```

## How to Run

```bash
cd examples/langgraph-integration/design_patterns/supervisor/toy/
python main.py
```

## Expected Output

### Test 1: Parallel Execution
```
🧭 SUPERVISOR DECISION: 'parallel_route'
   → Routing to PARALLEL EXECUTION (Send API)

🚀 SEND API: Fanning out to 3 agents IN PARALLEL
   Start time: 14:23:45.123

🤖 DATA AGENT ACTIVATED
📊 [DATA AGENT] Starting financial_metrics analysis at 14:23:45.456

🤖 RESEARCH AGENT ACTIVATED
🔍 [RESEARCH AGENT] Starting market_trends research at 14:23:45.458

🤖 PROCESSING AGENT ACTIVATED
⚙️ [PROCESSING AGENT] Starting user_data at 14:23:45.460

✅ [DATA AGENT] Finished at 14:23:47.456
✅ [RESEARCH AGENT] Finished at 14:23:47.458
✅ [PROCESSING AGENT] Finished at 14:23:47.460

⏱️  Total time: 2.05 seconds
🎉 Speedup: 2.9x faster than sequential!
```

### Test 2: Single Execution
```
🧭 SUPERVISOR DECISION: 'single_route'
   → Routing to SINGLE AGENT

🤖 SINGLE AGENT ACTIVATED
📊 [DATA AGENT] Starting simple_task analysis at 14:23:50.123
✅ [DATA AGENT] Finished at 14:23:52.123

⏱️  Total time: 2.08 seconds
```

## Key Takeaways

1. **Conditional edges** let the supervisor route dynamically based on LLM decisions
2. **Send API** enables true parallel execution (not sequential)
3. **Timing validation** proves parallelism with ~3x speedup
4. **AsyncFlow integration** handles all async orchestration seamlessly

## Cleanup

This is throwaway/test code - delete when done validating the patterns!
