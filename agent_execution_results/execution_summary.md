# 📊 LangGraph Execution Report

**Generated on:** `2025-10-29 15:54:39`
**Total Duration:** `0.879 seconds`

## 📈 Aggregate Statistics

- **Total Tokens Used:** `0`
- **Total Tool Calls:** `0`
- **Total Tool Executions:** `0`
- **Total Messages:** `0`
- **Models Used:** `None`
- **Number of Nodes:** `3`

--- 

## 🧠 Memory Statistics

- **Total Messages:** `1`
- **Memory Efficiency:** `2.0%`
- **Average Importance:** `1.04`
- **System Messages:** `0`
- **Human Messages:** `0`
- **AI Messages:** `1`
- **Interaction Count:** `1`
- **Memory Operations:** `1`
  - Operations: `preprocessing_memory_update`

--- 

## 📝 Execution Summary

| Node Name           | Duration (s) | Tokens | Tools | New Messages |
|---------------------|--------------|--------|-------|---------------|
| `preprocess` | 0.1440       | 0      | 0     | 0             |
| **preprocess Total** | 0.1440     | 0    | 0   | 0           |
| `research_agent` | 0.6563       | 0      | 0     | 0             |
| **research_agent Total** | 0.6563     | 0    | 0   | 0           |
| `context_preparation` | not visited | <n/a>  | <n/a> | <n/a>         |
| `synthesis_agent` | not visited | <n/a>  | <n/a> | <n/a>         |
| `finalize_output` | not visited | <n/a>  | <n/a> | <n/a>         |
| `error_handler` | 0.0016       | 0      | 0     | 0             |
| **error_handler Total** | 0.0016     | 0    | 0   | 0           |


## 🔍 Node Details

--- 

### 1. Node: `preprocess`

**Description:**
```
Memory-aware preprocessing node.
```

- **Timestamp:** `15:54:39.412`
- **Duration:** `0.144 seconds`
- **Messages Before/After:** `0` → `0` (➕ 0)
- **State Keys:** `user_input, user_id, validation_data, preprocessing_complete, research_agent_output, synthesis_agent_output, messages, context, memory_context, memory_stats, relevant_memory, final_output, workflow_complete, errors, current_stage, memory_operations`

**📊 Token Usage:**
- **Input Tokens:** `0`
- **Output Tokens:** `0`
- **Total Tokens:** `0`

**🔄 State Changes:**
```json
{
  "memory_stats": {
    "changed_from": "None",
    "changed_to": "[{'total_messages': 1, 'system_messages': 0, 'human_messages': 0, 'ai_messages': 1, 'memory_efficiency': 0.02, 'average_importance': 1.041, 'interaction_count': 1}]"
  },
  "preprocessing_complete": {
    "changed_from": "False",
    "changed_to": "True"
  },
  "validation_data": {
    "changed_from": "None",
    "changed_to": "[{'is_valid': True, 'cleaned_input': 'I need to research the latest developments in renewable energy storage technologies \\nand create a comprehensive report with recommendations for a clean energy startup \\nfocusing on battery technologies, grid integration, and market opportunities.', 'word_count': 33, 'timestamp': 0.607022958, 'metadata': {'domain': 'energy', 'complexity': 'high'}}]"
  },
  "memory_operations": {
    "changed_from": "[[]]",
    "changed_to": "[['preprocessing_memory_update']]"
  },
  "current_stage": {
    "changed_from": "initialized",
    "changed_to": "preprocessing_complete"
  }
}
```

--- 

### 2. Node: `research_agent`

**Description:**
```
Memory-aware research agent node.
```

- **Timestamp:** `15:54:39.557`
- **Duration:** `0.6563 seconds`
- **Messages Before/After:** `0` → `0` (➕ 0)
- **State Keys:** `user_input, user_id, validation_data, preprocessing_complete, research_agent_output, synthesis_agent_output, messages, context, memory_context, memory_stats, relevant_memory, final_output, workflow_complete, errors, current_stage, memory_operations`

**📊 Token Usage:**
- **Input Tokens:** `0`
- **Output Tokens:** `0`
- **Total Tokens:** `0`

**🔄 State Changes:**
```json
{
  "errors": {
    "changed_from": "[[]]",
    "changed_to": "[[\"Research error: Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}\"]]"
  },
  "current_stage": {
    "changed_from": "preprocessing_complete",
    "changed_to": "research_failed"
  }
}
```

--- 

### 3. Node: `error_handler`

**Description:**
```
Handle errors with memory context.
```

- **Timestamp:** `15:54:40.215`
- **Duration:** `0.0016 seconds`
- **Messages Before/After:** `0` → `0` (➕ 0)
- **State Keys:** `user_input, user_id, validation_data, preprocessing_complete, research_agent_output, synthesis_agent_output, messages, context, memory_context, memory_stats, relevant_memory, final_output, workflow_complete, errors, current_stage, memory_operations`

**📊 Token Usage:**
- **Input Tokens:** `0`
- **Output Tokens:** `0`
- **Total Tokens:** `0`

**🔄 State Changes:**
```json
{
  "workflow_complete": {
    "changed_from": "False",
    "changed_to": "True"
  },
  "final_output": {
    "changed_from": "",
    "changed_to": "\nWorkflow encountered errors:\n- Research error: Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}\n\nCurrent stage: research_failed\nMemory operations completed: 1\n"
  }
}
```

--- 

## ✅ Final State Summary

**State Keys:** `user_input, user_id, validation_data, preprocessing_complete, research_agent_output, synthesis_agent_output, messages, context, memory_context, memory_stats, relevant_memory, final_output, workflow_complete, errors, current_stage, memory_operations`

- **user_input:** I need to research the latest developments in renewable energy storage technologies 
and create a comprehensive report with recommendations for a clean energy startup 
focusing on battery technologies, grid integration, and market opportunities.
- **user_id:** research_user_001
- **validation_data:** is_valid=True cleaned_input='I need to research the latest developments in renewable energy storage technologies \nand create a comprehensive report with recommendations for a clean energy startup \nfocusing on battery technologies, grid integration, and market opportunities.' word_count=33 timestamp=0.607022958 metadata={'domain': 'energy', 'complexity': 'high'}
- **preprocessing_complete:** True
- **research_agent_output:** None
- **synthesis_agent_output:** None
- **messages:** []
- **context:** None
- **memory_context:** {}
- **memory_stats:** total_messages=1 system_messages=0 human_messages=0 ai_messages=1 memory_efficiency=0.02 average_importance=1.041 interaction_count=1
- **relevant_memory:** []
- **final_output:** 
Workflow encountered errors:
- Research error: Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}

Current stage: research_failed
Memory operations completed: 1

- **workflow_complete:** True
- **errors:** ["Research error: Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}"]
- **current_stage:** research_failed
- **memory_operations:** ['preprocessing_memory_update']
