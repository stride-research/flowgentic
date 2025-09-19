"""
Minimal LangGraph example demonstrating parallel tool execution using AsyncFlow integration.

This example shows:
1. LLM makes a decision and calls multiple tools
2. Tools execute in parallel through AsyncFlow
3. Results are collected and returned to LLM
"""

import asyncio
import os
from typing import List, Annotated, Optional, Dict, Any
from pydantic import BaseModel, Field
import logging
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

from concurrent.futures import ThreadPoolExecutor
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowgentic.langgraph import LangGraphIntegration, RetryConfig

from radical.asyncflow.logging import init_default_logger

logger = logging.getLogger(__name__)

init_default_logger(logging.INFO)

load_dotenv()

# -------------------------
# Enhanced State models
# -------------------------
class ToolCallResult(BaseModel):
    tool_name: Optional[str] = None
    args: Optional[Dict[str, Any]] = None
    result: Optional[str] = None
    success: bool = True
    error: Optional[str] = None

class State(BaseModel):
    # allow arbitrary types so BaseMessage etc. are accepted
    model_config = {"arbitrary_types_allowed": True}

    # conversation messages (same as before)
    messages: Annotated[List[BaseMessage], add_messages] = Field(default_factory=list)

    # enhanced bookkeeping
    tool_calls: List[ToolCallResult] = Field(default_factory=list)
    intermediate_results: List[Dict[str, Any]] = Field(default_factory=list)

    # index (in messages list) of the last LLM message that requested tool calls
    last_llm_tool_msg_index: Optional[int] = None

# -------------------------
# Main / AsyncFlow / Tools
# -------------------------
async def main():

    # Initialize AsyncFlow
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())
    flow = await WorkflowEngine.create(backend=backend)
    integration = LangGraphIntegration(flow)

    # Define AsyncFlow tools that will execute in parallel
    @integration.asyncflow_tool(
        retry=RetryConfig(max_attempts=3, base_backoff_sec=0.5, max_backoff_sec=4.0, timeout_sec=10.0)
    )
    async def fetch_weather(city: str) -> str:
        """Get weather for a city."""
        logger.info(f"[AsyncFlow] Fetching weather for {city}...")
        await asyncio.sleep(2)  # Simulate API call
        return f"Weather in {city}: Sunny, 22°C"

    @integration.asyncflow_tool(
        # Simulate a slightly more patient policy for news
        retry=RetryConfig(max_attempts=4, base_backoff_sec=0.5, max_backoff_sec=6.0, timeout_sec=15.0)
    )
    async def fetch_news(topic: str) -> str:
        """Get news about a topic."""
        logger.info(f"[AsyncFlow] Fetching news about {topic}...")
        await asyncio.sleep(1.5)  # Simulate API call
        return f"Latest news on {topic}: Market trends looking positive"

    @integration.asyncflow_tool(
        retry=RetryConfig(max_attempts=2, base_backoff_sec=0.4, max_backoff_sec=2.0, timeout_sec=8.0)
    )
    async def calculate_distance(city1: str, city2: str) -> str:
        """Calculate distance between cities."""
        logger.info(f"[AsyncFlow] Calculating distance {city1} -> {city2}...")
        await asyncio.sleep(1)  # Simulate calculation
        return f"Distance from {city1} to {city2}: 245 km"

    tools = [fetch_weather, fetch_news, calculate_distance]

    # -------------------------
    # LangGraph nodes
    # -------------------------
    async def llm_node(state: State):
        """LLM decides what to do and calls tools."""
        llm = ChatOpenAI(
            temperature=0.3,
            model="google/gemini-2.5-pro",
            openai_api_base="https://openrouter.ai/api/v1",
            openai_api_key=os.getenv("OPEN_ROUTER_API_KEY"),
            max_retries=3,
            timeout=30,
        )

        llm_with_tools = llm.bind_tools(tools)
        response = await llm_with_tools.ainvoke(state["messages"])

        # --- persist detected tool-calls into state.tool_calls ---
        # Many tool-call objects differ in shape across integrations, so try several access patterns.
        detected_calls = []
        if hasattr(response, "tool_calls") and response.tool_calls:
            raw_calls = response.tool_calls
        elif isinstance(response, dict) and response.get("tool_calls"):
            raw_calls = response["tool_calls"]
        else:
            raw_calls = []

        for call in raw_calls:
            # normalize the call into ToolCallResult
            try:
                # try common attribute names
                name = getattr(call, "tool_name", None) or getattr(call, "name", None)
                args = getattr(call, "args", None) or getattr(call, "kwargs", None)
                # if call is dict-like
                if name is None and hasattr(call, "get"):
                    name = call.get("tool_name") or call.get("name")
                    args = args or call.get("args") or call.get("kwargs")
                # fallback to string representation
                if name is None:
                    name = str(call)
            except Exception:
                name = str(call)
                args = None

            detected_calls.append(ToolCallResult(tool_name=name, args=args))

        if detected_calls:
            # record index (the LLM response will be appended to messages list by the StateGraph)
            # current length of messages is the index where this response will be appended (i.e. len before append)
            state_index = len(state["messages"])
            state.last_llm_tool_msg_index = state_index
            # append normalized tool calls to state
            state.tool_calls.extend(detected_calls)
            logger.info(f"Recorded {len(detected_calls)} tool call(s) at messages index {state_index}.")

        # return the LLM response as before (so the graph's messages array is updated)
        return {"messages": [response]}

    def should_continue(state: State):
        """Check if we should continue to tools or end."""
        last_message = state["messages"][-1]
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            return "tools"
        return "end"

    # Node that captures tool outputs and writes intermediate_results into the State
    def capture_tools(state: State):
        """
        After ToolNode runs, tool result messages should be appended into state.messages.
        This node will:
        - collect messages appended after last_llm_tool_msg_index into intermediate_results
        - attempt to attach result text to corresponding entries in state.tool_calls (by order
          or by simple name matching).
        """
        if state.last_llm_tool_msg_index is None:
            # nothing to capture
            return {}

        all_msgs = state["messages"]
        start_idx = state.last_llm_tool_msg_index
        # messages after the LLM tool-call message (tool outputs should be here)
        new_msgs = all_msgs[start_idx + 1 : ]

        if not new_msgs:
            logger.info("No intermediate messages produced by tools to capture.")
            return {}

        logger.info(f"Capturing {len(new_msgs)} intermediate result message(s) from index {start_idx+1} to {len(all_msgs)-1}")

        # store raw message contents + metadata
        for m in new_msgs:
            try:
                content = getattr(m, "content", str(m))
            except Exception:
                content = str(m)
            metadata = getattr(m, "metadata", None) if hasattr(m, "metadata") else None
            entry = {"content": content, "metadata": metadata}
            state.intermediate_results.append(entry)

        # Try to attach results back to tool_calls in order (best-effort)
        # 1) by name matching: if tool name appears in content, attach
        # 2) otherwise, do positional mapping
        unmatched_tool_indices = [i for i, tc in enumerate(state.tool_calls) if tc.result is None]

        # name matching
        for idx in unmatched_tool_indices[:]:
            tc = state.tool_calls[idx]
            if not tc.tool_name:
                continue
            for msg in new_msgs:
                txt = getattr(msg, "content", "")
                if tc.tool_name.lower() in txt.lower():
                    tc.result = txt
                    unmatched_tool_indices.remove(idx)
                    break

        # positional fallback
        if unmatched_tool_indices:
            # map remaining unmatched tools in order to remaining messages
            remaining_msgs = [getattr(m, "content", "") for m in new_msgs]
            rm_idx = 0
            for idx in unmatched_tool_indices:
                if rm_idx < len(remaining_msgs):
                    state.tool_calls[idx].result = remaining_msgs[rm_idx]
                    rm_idx += 1
                else:
                    break

        logger.info(f"Updated tool_calls with results where available. Tool calls now: {state.tool_calls}")

        # once we've captured these results, optionally clear last index so we don't re-capture on loops
        state.last_llm_tool_msg_index = None

        # return the updated fields to the graph runtime
        return {
            "intermediate_results": state.intermediate_results,
            "tool_calls": state.tool_calls,
            "last_llm_tool_msg_index": state.last_llm_tool_msg_index,
        }

    # Build graph
    workflow = StateGraph(State)
    workflow.add_node("llm", llm_node)

    # Keep your ToolNode in place (it executes tools in parallel via AsyncFlow integration)
    workflow.add_node("tools", ToolNode(tools))

    # Add our capture node that persists tool outputs & intermediate results
    workflow.add_node("capture", capture_tools)

    # wiring: llm -> (maybe) tools -> capture -> llm OR end
    workflow.set_entry_point("llm")
    workflow.add_conditional_edges("llm", should_continue, {"tools": "tools", "end": END})

    # connect tools -> capture -> llm (replaces direct tools->llm edge so we can persist intermediate results)
    workflow.add_edge("tools", "capture")
    workflow.add_edge("capture", "llm")

    # Enable checkpointing so the graph can resume gracefully
    app = workflow.compile()

    # Test the parallel execution
    logger.info("🚀 Testing parallel tool execution...")
    logger.info("=" * 50)

    user_message = """I'm planning a trip from Paris to London. Can you:
    1. Get the weather in London
    2. Find news about travel
    3. Calculate the distance between Paris and London

    Please do all of these at once."""

    logger.info(f"User: {user_message}")
    logger.info("\n🤖 LLM Response and Tool Execution:")

    start_time = asyncio.get_event_loop().time()

    # Run the workflow - tools will execute in parallel through AsyncFlow
    final_state = await app.ainvoke({
        "messages": [HumanMessage(content=user_message)]
    })

    end_time = asyncio.get_event_loop().time()

    # Show final response and the recorded tool interactions
    final_response = final_state["messages"][-1]
    logger.info(f"\n✅ Final Response: {final_response.content}")
    logger.info(f"⏱️ Total execution time: {end_time - start_time:.2f} seconds")
    logger.info("\n💡 Note: The tools executed in parallel through AsyncFlow!")
    logger.info("   If they ran sequentially, it would take ~4.5 seconds")
    logger.info("   With parallel execution, it should be closer to ~2 seconds")

    # Show what we recorded
    logger.info("\n📦 Recorded tool_calls:")
    for t in final_state.get("tool_calls", []):
        logger.info(f" - {t.tool_name} | args={t.args} | result={t.result}")

    logger.info("\n🧩 Intermediate results (raw messages from tools):")
    for r in final_state.get("intermediate_results", []):
        logger.info(f" - {r}")

    await flow.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
