# MULTI-AGENT GRAPH

![Graph](agent_graph.png)

# 📊 LangGraph Execution Report

**Generated on:** `2025-10-16 19:58:56`
**Total Duration:** `5.311 seconds`

## 📈 Aggregate Statistics

- **Total Tokens Used:** `0`
- **Total Tool Calls:** `0`
- **Total Tool Executions:** `0`
- **Total Messages:** `0`
- **Models Used:** `None`
- **Number of Nodes:** `1`

--- 

## 📝 Execution Summary

| Node Name           | Duration (s) | Tokens | Tools | New Messages |
|---------------------|--------------|--------|-------|---------------|
| `llm_router_20251016-195859-178` | 1.7081       | 0      | 0     | 0             |
| **Total:llm_router** | 1.7081     | 0    | 0   | 0           |
| `agent_A` | not visited | <n/a>  | <n/a> | <n/a>         |
| `agent_B` | not visited | <n/a>  | <n/a> | <n/a>         |
| `gather` | not visited | <n/a>  | <n/a> | <n/a>         |


## 🔍 Node Details

--- 

### 1. Node: `llm_router`

**Description:**
```
LLM decides which agent(s) to route to based on user query.
```

- **Timestamp:** `19:58:57.470`
- **Duration:** `1.7081 seconds`
- **Messages Before/After:** `0` → `0` (➕ 0)
- **State Keys:** `query, routing_decision, results, final_summary, messages`

**📊 Token Usage:**
- **Input Tokens:** `0`
- **Output Tokens:** `0`
- **Total Tokens:** `0`

**🔄 State Changes:**
```json
{
  "routing_decision": {
    "changed_from": "None",
    "changed_to": "[['agent_A', 'agent_B']]"
  }
}
```

--- 

## ✅ Final State Summary

