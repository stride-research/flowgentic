# 📊 LangGraph Execution Report

**Generated on:** `2025-10-30 11:21:38`
**Total Duration:** `1.1306 seconds`

## 📈 Aggregate Statistics

- **Total Tokens Used:** `0`
- **Total Tool Calls:** `0`
- **Total Tool Executions:** `0`
- **Total Messages:** `0`
- **Models Used:** `None`
- **Number of Nodes:** `3`

--- 

## 📝 Execution Summary

| Node Name           | Duration (s) | Tokens | Tools | New Messages |
|---------------------|--------------|--------|-------|---------------|
| `preprocess_20251030-112138-118` | 0.0443       | 0      | 0     | 0             |
| **Total:preprocess** | 0.0443     | 0    | 0   | 0           |
| `research_agent_20251030-112139-185` | 1.0652       | 0      | 0     | 0             |
| **Total:research_agent** | 1.0652     | 0    | 0   | 0           |
| `context_preparation` | not visited | <n/a>  | <n/a> | <n/a>         |
| `synthesis_agent` | not visited | <n/a>  | <n/a> | <n/a>         |
| `finalize_output` | not visited | <n/a>  | <n/a> | <n/a>         |
| `error_handler_20251030-112139-191` | 0.0025       | 0      | 0     | 0             |
| **Total:error_handler** | 0.0025     | 0    | 0   | 0           |


## 🔍 Node Details

--- 

### 1. Node: `preprocess`

**Description:**
```
Preprocessing node with parallel validation and metadata extraction.
```

- **Timestamp:** `11:21:38.074`
- **Duration:** `0.0443 seconds`
- **Messages Before/After:** `0` → `0` (➕ 0)
- **State Keys:** `user_input, validation_data, preprocessing_complete, research_agent_output, synthesis_agent_output, messages, context, final_output, workflow_complete, errors, current_stage`

**📊 Token Usage:**
- **Input Tokens:** `0`
- **Output Tokens:** `0`
- **Total Tokens:** `0`

**🔄 State Changes:**
```json
{
  "validation_data": {
    "changed_from": "None",
    "changed_to": "[{'is_valid': True, 'cleaned_input': 'I need to research the latest developments in renewable energy storage technologies \\n            and create a comprehensive report with recommendations for a clean energy startup \\n            focusing on battery technologies, grid integration, and market opportunities.', 'word_count': 33, 'timestamp': 0.439204666, 'metadata': {'has_keywords': True, 'complexity_score': 1.0, 'domain': 'energy'}}]"
  },
  "current_stage": {
    "changed_from": "initialized",
    "changed_to": "preprocessing_complete"
  },
  "preprocessing_complete": {
    "changed_from": "False",
    "changed_to": "True"
  }
}
```

--- 

### 2. Node: `research_agent`

**Description:**
```
Research agent execution node.
```

- **Timestamp:** `11:21:38.120`
- **Duration:** `1.0652 seconds`
- **Messages Before/After:** `0` → `0` (➕ 0)
- **State Keys:** `user_input, validation_data, preprocessing_complete, research_agent_output, synthesis_agent_output, messages, context, final_output, workflow_complete, errors, current_stage`

**📊 Token Usage:**
- **Input Tokens:** `0`
- **Output Tokens:** `0`
- **Total Tokens:** `0`

**🔄 State Changes:**
```json
{
  "research_agent_output": {
    "changed_from": "None",
    "changed_to": "[{'agent_name': 'Research Agent', 'output_content': '', 'execution_time': 0.0, 'tools_used': [], 'success': False, 'error_message': \"Research agent error: Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}\"}]"
  },
  "errors": {
    "changed_from": "[[]]",
    "changed_to": "[[\"Research agent error: Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}\"]]"
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
Handle errors in the workflow.
```

- **Timestamp:** `11:21:39.188`
- **Duration:** `0.0025 seconds`
- **Messages Before/After:** `0` → `0` (➕ 0)
- **State Keys:** `user_input, validation_data, preprocessing_complete, research_agent_output, synthesis_agent_output, messages, context, final_output, workflow_complete, errors, current_stage`

**📊 Token Usage:**
- **Input Tokens:** `0`
- **Output Tokens:** `0`
- **Total Tokens:** `0`

**🔄 State Changes:**
```json
{
  "final_output": {
    "changed_from": "",
    "changed_to": "Workflow failed with errors: Research agent error: Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}"
  },
  "current_stage": {
    "changed_from": "research_failed",
    "changed_to": "error_handled"
  }
}
```

--- 

## ✅ Final State Summary

**State Keys:** `user_input, validation_data, preprocessing_complete, research_agent_output, messages, final_output, workflow_complete, errors, current_stage`

- **user_input:** 
            I need to research the latest developments in renewable energy storage technologies 
            and create a comprehensive report with recommendations for a clean energy startup 
            focusing on battery technologies, grid integration, and market opportunities.
            
- **validation_data:** is_valid=True cleaned_input='I need to research the latest developments in renewable energy storage technologies \n            and create a comprehensive report with recommendations for a clean energy startup \n            focusing on battery technologies, grid integration, and market opportunities.' word_count=33 timestamp=0.439204666 metadata={'has_keywords': True, 'complexity_score': 1.0, 'domain': 'energy'}
- **preprocessing_complete:** True
- **research_agent_output:** agent_name='Research Agent' output_content='' execution_time=0.0 tools_used=[] success=False error_message="Research agent error: Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}"
- **messages:** []
- **final_output:** Workflow failed with errors: Research agent error: Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}
- **workflow_complete:** False
- **errors:** ["Research agent error: Error code: 401 - {'error': {'message': 'User not found.', 'code': 401}}"]
- **current_stage:** error_handled
