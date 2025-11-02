# MULTI-AGENT GRAPH

![Graph](agent_graph.png)

# 📊 LangGraph Execution Report

**Generated on:** `2025-11-02 11:23:10`
**Total Duration:** `13.6507 seconds`

## 📈 Aggregate Statistics

- **Total Tokens Used:** `153`
- **Total Tool Calls:** `0`
- **Total Tool Executions:** `0`
- **Total Messages:** `6`
- **Models Used:** `google/gemini-2.5-flash`
- **Number of Nodes:** `4`

--- 

## 📝 Execution Summary

| Node Name           | Duration (s) | Tokens | Tools | New Messages |
|---------------------|--------------|--------|-------|---------------|
| `llm_router_20251102-112320-583` | 9.6874       | 0      | 0     | 3             |
| **Total:llm_router** | 9.6874     | 0    | 0   | 0           |
| `agent_A_20251102-112323-596` | 3.0091       | 0      | 0     | -3            |
| **Total:agent_A** | 3.0091     | 0    | 0   | 0           |
| `agent_B_20251102-112323-597` | 3.0094       | 0      | 0     | -3            |
| **Total:agent_B** | 3.0094     | 0    | 0   | 0           |
| `gather_20251102-112324-338` | 0.7370       | 153    | 0     | 3             |
| **Total:gather** | 0.7370     | 153  | 0   | 0           |


## 🔍 Node Details

--- 

### 1. Node: `llm_router`

**Description:**
```
LLM decides which agent(s) to route to based on user query.
```

- **Timestamp:** `11:23:10.895`
- **Duration:** `9.6874 seconds`
- **Messages Before/After:** `0` → `3` (➕ 3)
- **State Keys:** `query, routing_decision, routing_rationale, results, final_summary, messages`

**🤖 Model Information:**
- **Model Name:** `None`

**📊 Token Usage:**
- **Input Tokens:** `0`
- **Output Tokens:** `0`
- **Total Tokens:** `0`

 **FULL CONVERSATION HISTORY FOR llm_router:**

**💬 Messages Added (3):**
1. **SystemMessage**
   - **Content:** `You are a routing assistant. Analyze queries and decide which agent(s) should handle them.`
2. **HumanMessage**
   - **Content:** `
Based on the user's query, decide which agent(s) should handle it:



- agent_A: Handles data processing and analysis tasks
- agent_B: Handles question answering and information retrieval
- both: Whe...`
3. **AIMessage**
   - **Content:** `Routing Decision: ['agent_A', 'agent_B']
Rationale: The user's query is a compound request. 'Analyze the data' requires the data processing capabilities of agent_A, while 'explain what parallelism mea...`

**🔄 State Changes:**
```json
{
  "routing_decision": {
    "changed_from": "None",
    "changed_to": "[['agent_A', 'agent_B']]"
  },
  "messages": {
    "changed_from": "[[]]",
    "changed_to": "[[{'content': 'You are a routing assistant. Analyze queries and decide which agent(s) should handle them.', 'additional_kwargs': {}, 'response_metadata': {}, 'type': 'system', 'name': None, 'id': None}, {'content': '\\nBased on the user\\'s query, decide which agent(s) should handle it:\\n\\n\\n\\n- agent_A: Handles data processing and analysis tasks\\n- agent_B: Handles question answering and information retrieval\\n- both: When the query requires both processing AND answering\\n\\n\\n\\nUser query: \"Analyze the data and explain what parallelism means\"\\n\\nNotes:\\n\\t- You can select multiple agents to work in parallel\\n\\t- You can select just one agent if that\\'s most appropriate\\n\\t- Explain your reasoning for the selection\\n', 'additional_kwargs': {}, 'response_metadata': {}, 'type': 'human', 'name': None, 'id': None}, {'content': \"Routing Decision: ['agent_A', 'agent_B']\\nRationale: The user's query is a compound request. 'Analyze the data' requires the data processing capabilities of agent_A, while 'explain what parallelism means' is a request for information that should be handled by agent_B.\", 'additional_kwargs': {}, 'response_metadata': {}, 'type': 'ai', 'name': None, 'id': None}]]"
  },
  "routing_rationale": {
    "changed_from": "None",
    "changed_to": "The user's query is a compound request. 'Analyze the data' requires the data processing capabilities of agent_A, while 'explain what parallelism means' is a request for information that should be handled by agent_B."
  }
}
```

--- 

### 2. Node: `agent_A`

- **Timestamp:** `11:23:20.587`
- **Duration:** `3.0091 seconds`
- **Messages Before/After:** `3` → `0` (➕ -3)
- **State Keys:** `results`

**📊 Token Usage:**
- **Input Tokens:** `0`
- **Output Tokens:** `0`
- **Total Tokens:** `0`

**🔄 State Changes:**
```json
{
  "results": {
    "changed_from": "[{}]",
    "changed_to": "[{'agent_A': 'Data analysis complete: Analyze the data and explain what parallelism means'}]"
  }
}
```

--- 

### 3. Node: `agent_B`

- **Timestamp:** `11:23:20.588`
- **Duration:** `3.0094 seconds`
- **Messages Before/After:** `3` → `0` (➕ -3)
- **State Keys:** `results`

**📊 Token Usage:**
- **Input Tokens:** `0`
- **Output Tokens:** `0`
- **Total Tokens:** `0`

**🔄 State Changes:**
```json
{
  "results": {
    "changed_from": "[{}]",
    "changed_to": "[{'agent_b': 'Answer: Analyze the data and explain what parallelism means - Parallelism is executing multiple tasks simultaneously!'}]"
  }
}
```

--- 

### 4. Node: `gather`

**Description:**
```
Gather and synthesize results from all executed agents using LLM.
```

- **Timestamp:** `11:23:23.601`
- **Duration:** `0.737 seconds`
- **Messages Before/After:** `3` → `6` (➕ 3)
- **State Keys:** `query, routing_decision, routing_rationale, results, final_summary, messages`

**🤖 Model Information:**
- **Model Name:** `google/gemini-2.5-flash`
- **Finish Reason:** `stop`

**📊 Token Usage:**
- **Input Tokens:** `102`
- **Output Tokens:** `51`
- **Total Tokens:** `153`

**📥 Model Final Response:**
```text
Data analysis is complete. Parallelism, in the context of data processing, refers to the ability to execute multiple tasks or processes simultaneously. This approach significantly speeds up computation by dividing a large problem into smaller, independently solvable parts that can be worked on concurrently.
```

 **FULL CONVERSATION HISTORY FOR gather:**

**💬 Messages Added (3):**
1. **SystemMessage** (ID: `1ed4893e-e240-49ae-b...`)
   - **Content:** `You are a synthesis agent that combines multiple agent outputs into a coherent response.`
2. **HumanMessage** (ID: `e57f3737-7d9c-4131-9...`)
   - **Content:** `
You are a synthesis agent. Combine the following outputs from two parallel agents into a coherent, comprehensive response.

Agent A (Data Processing): Data analysis complete: Analyze the data and exp...`
3. **AIMessage** (ID: `run--51213b24-0655-4...`)
   - **Content:** `Data analysis is complete. Parallelism, in the context of data processing, refers to the ability to execute multiple tasks or processes simultaneously. This approach significantly speeds up computatio...`

**🔄 State Changes:**
```json
{
  "messages": {
    "changed_from": "[[{'content': 'You are a routing assistant. Analyze queries and decide which agent(s) should handle them.', 'additional_kwargs': {}, 'response_metadata': {}, 'type': 'system', 'name': None, 'id': 'd2863235-88d5-4f53-bd6e-76904ce707e0'}, {'content': '\\nBased on the user\\'s query, decide which agent(s) should handle it:\\n\\n\\n\\n- agent_A: Handles data processing and analysis tasks\\n- agent_B: Handles question answering and information retrieval\\n- both: When the query requires both processing AND answering\\n\\n\\n\\nUser query: \"Analyze the data and explain what parallelism means\"\\n\\nNotes:\\n\\t- You can select multiple agents to work in parallel\\n\\t- You can select just one agent if that\\'s most appropriate\\n\\t- Explain your reasoning for the selection\\n', 'additional_kwargs': {}, 'response_metadata': {}, 'type': 'human', 'name': None, 'id': '81c53ee8-6ce2-41bc-bca3-1f1d0f2f814f'}, {'content': \"Routing Decision: ['agent_A', 'agent_B']\\nRationale: The user's query is a compound request. 'Analyze the data' requires the data processing capabilities of agent_A, while 'explain what parallelism means' is a request for information that should be handled by agent_B.\", 'additional_kwargs': {}, 'response_metadata': {}, 'type': 'ai', 'name': None, 'id': 'c1fd6584-c30a-4133-8164-7dfb3dd9fa0d'}]]",
    "changed_to": "[[{'content': 'You are a routing assistant. Analyze queries and decide which agent(s) should handle them.', 'additional_kwargs': {}, 'response_metadata': {}, 'type': 'system', 'name': None, 'id': 'd2863235-88d5-4f53-bd6e-76904ce707e0'}, {'content': '\\nBased on the user\\'s query, decide which agent(s) should handle it:\\n\\n\\n\\n- agent_A: Handles data processing and analysis tasks\\n- agent_B: Handles question answering and information retrieval\\n- both: When the query requires both processing AND answering\\n\\n\\n\\nUser query: \"Analyze the data and explain what parallelism means\"\\n\\nNotes:\\n\\t- You can select multiple agents to work in parallel\\n\\t- You can select just one agent if that\\'s most appropriate\\n\\t- Explain your reasoning for the selection\\n', 'additional_kwargs': {}, 'response_metadata': {}, 'type': 'human', 'name': None, 'id': '81c53ee8-6ce2-41bc-bca3-1f1d0f2f814f'}, {'content': \"Routing Decision: ['agent_A', 'agent_B']\\nRationale: The user's query is a compound request. 'Analyze the data' requires the data processing capabilities of agent_A, while 'explain what parallelism means' is a request for information that should be handled by agent_B.\", 'additional_kwargs': {}, 'response_metadata': {}, 'type': 'ai', 'name': None, 'id': 'c1fd6584-c30a-4133-8164-7dfb3dd9fa0d'}, {'content': 'You are a synthesis agent that combines multiple agent outputs into a coherent response.', 'additional_kwargs': {}, 'response_metadata': {}, 'type': 'system', 'name': None, 'id': '1ed4893e-e240-49ae-bbab-5926b631eef5'}, {'content': '\\nYou are a synthesis agent. Combine the following outputs from two parallel agents into a coherent, comprehensive response.\\n\\nAgent A (Data Processing): Data analysis complete: Analyze the data and explain what parallelism means\\n\\nAgent B (Question Answering): N/A\\n\\nOriginal Query: Analyze the data and explain what parallelism means\\n\\nCreate a brief, unified response that integrates both perspectives. Keep it concise (2-3 sentences).\\n', 'additional_kwargs': {}, 'response_metadata': {}, 'type': 'human', 'name': None, 'id': 'e57f3737-7d9c-4131-9f83-1f6c216373b1'}, {'content': 'Data analysis is complete. Parallelism, in the context of data processing, refers to the ability to execute multiple tasks or processes simultaneously. This approach significantly speeds up computation by dividing a large problem into smaller, independently solvable parts that can be worked on concurrently.', 'additional_kwargs': {'refusal': None}, 'response_metadata': {'token_usage': {'completion_tokens': 51, 'prompt_tokens': 102, 'total_tokens': 153, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1762079003-Rf92cta0bwkTXnye5vSr', 'service_tier': None, 'finish_reason': 'stop', 'logprobs': None}, 'type': 'ai', 'name': None, 'id': 'run--51213b24-0655-4493-b210-4441e0fe0b64-0'}]]"
  },
  "final_summary": {
    "changed_from": "None",
    "changed_to": "Data analysis is complete. Parallelism, in the context of data processing, refers to the ability to execute multiple tasks or processes simultaneously. This approach significantly speeds up computation by dividing a large problem into smaller, independently solvable parts that can be worked on concu"
  }
}
```

--- 

## ✅ Final State Summary

**State Keys:** `query, routing_decision, routing_rationale, results, final_summary, messages`

- **query:** Analyze the data and explain what parallelism means
- **routing_decision:** ['agent_A', 'agent_B']
- **routing_rationale:** The user's query is a compound request. 'Analyze the data' requires the data processing capabilities of agent_A, while 'explain what parallelism means' is a request for information that should be handled by agent_B.
- **results:** {'agent_A': 'Data analysis complete: Analyze the data and explain what parallelism means', 'agent_b': 'Answer: Analyze the data and explain what parallelism means - Parallelism is executing multiple tasks simultaneously!'}
- **final_summary:** Data analysis is complete. Parallelism, in the context of data processing, refers to the ability to execute multiple tasks or processes simultaneously. This approach significantly speeds up computation by dividing a large problem into smaller, independently solvable parts that can be worked on concurrently.
- **messages:** [SystemMessage(content='You are a routing assistant. Analyze queries and decide which agent(s) should handle them.', additional_kwargs={}, response_metadata={}, id='d2863235-88d5-4f53-bd6e-76904ce707e0'), HumanMessage(content='\nBased on the user\'s query, decide which agent(s) should handle it:\n\n\n\n- agent_A: Handles data processing and analysis tasks\n- agent_B: Handles question answering and information retrieval\n- both: When the query requires both processing AND answering\n\n\n\nUser query: "Analyze the data and explain what parallelism means"\n\nNotes:\n\t- You can select multiple agents to work in parallel\n\t- You can select just one agent if that\'s most appropriate\n\t- Explain your reasoning for the selection\n', additional_kwargs={}, response_metadata={}, id='81c53ee8-6ce2-41bc-bca3-1f1d0f2f814f'), AIMessage(content="Routing Decision: ['agent_A', 'agent_B']\nRationale: The user's query is a compound request. 'Analyze the data' requires the data processing capabilities of agent_A, while 'explain what parallelism means' is a request for information that should be handled by agent_B.", additional_kwargs={}, response_metadata={}, id='c1fd6584-c30a-4133-8164-7dfb3dd9fa0d'), SystemMessage(content='You are a synthesis agent that combines multiple agent outputs into a coherent response.', additional_kwargs={}, response_metadata={}, id='1ed4893e-e240-49ae-bbab-5926b631eef5'), HumanMessage(content='\nYou are a synthesis agent. Combine the following outputs from two parallel agents into a coherent, comprehensive response.\n\nAgent A (Data Processing): Data analysis complete: Analyze the data and explain what parallelism means\n\nAgent B (Question Answering): N/A\n\nOriginal Query: Analyze the data and explain what parallelism means\n\nCreate a brief, unified response that integrates both perspectives. Keep it concise (2-3 sentences).\n', additional_kwargs={}, response_metadata={}, id='e57f3737-7d9c-4131-9f83-1f6c216373b1'), AIMessage(content='Data analysis is complete. Parallelism, in the context of data processing, refers to the ability to execute multiple tasks or processes simultaneously. This approach significantly speeds up computation by dividing a large problem into smaller, independently solvable parts that can be worked on concurrently.', additional_kwargs={'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 51, 'prompt_tokens': 102, 'total_tokens': 153, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1762079003-Rf92cta0bwkTXnye5vSr', 'service_tier': None, 'finish_reason': 'stop', 'logprobs': None}, id='run--51213b24-0655-4493-b210-4441e0fe0b64-0', usage_metadata={'input_tokens': 102, 'output_tokens': 51, 'total_tokens': 153, 'input_token_details': {'cache_read': 0}, 'output_token_details': {'reasoning': 0}})]
