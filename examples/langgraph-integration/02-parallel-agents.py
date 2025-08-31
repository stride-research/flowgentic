"""
LangGraph example demonstrating parallel agent execution using AsyncFlow integration.

This example shows:
1. Multiple specialized agents working in parallel
2. Each agent has its own tools and reasoning
3. AsyncFlow orchestrates parallel agent execution
4. Results are aggregated and synthesized
"""

import asyncio
import os
from typing import List, TypedDict, Annotated, Dict, Any
import logging
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

from concurrent.futures import ThreadPoolExecutor
from radical.asyncflow import ConcurrentExecutionBackend, WorkflowEngine

from flowcademy.langgraph import LangGraphIntegration, RetryConfig
from radical.asyncflow.logging import init_default_logger

logger = logging.getLogger(__name__)
init_default_logger(logging.INFO)
load_dotenv()

# Enhanced state for parallel agents
class MultiAgentState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    agent_results: Dict[str, Any]
    task_assignments: Dict[str, str]

async def main():
    # Initialize AsyncFlow
    backend = await ConcurrentExecutionBackend(ThreadPoolExecutor())
    flow = await WorkflowEngine.create(backend=backend)
    integration = LangGraphIntegration(flow)

    # --- Specialized Tools for Each Agent ---

    # Weather Agent Tools
    @integration.asyncflow_tool(
        retry=RetryConfig(max_attempts=3, base_backoff_sec=0.5, max_backoff_sec=6.0, timeout_sec=15.0)
    )
    async def get_detailed_weather(city: str) -> str:
        """Get detailed weather information including forecast."""
        logger.info(f"[Weather Agent] Analyzing weather for {city}...")
        await asyncio.sleep(2)
        return f"Weather in {city}: Sunny, 22°C. 3-day forecast: Sun/Rain/Cloudy. UV Index: 7"

    @integration.asyncflow_tool(
        retry=RetryConfig(max_attempts=3, base_backoff_sec=0.5, max_backoff_sec=4.0, timeout_sec=10.0)
    )
    async def get_weather_alerts(city: str) -> str:
        """Check for weather alerts and warnings."""
        logger.info(f"[Weather Agent] Checking alerts for {city}...")
        await asyncio.sleep(1)
        return f"Weather alerts for {city}: No severe weather warnings. Light rain expected tomorrow."

    # Travel Agent Tools
    @integration.asyncflow_tool(
        retry=RetryConfig(max_attempts=3, base_backoff_sec=0.5, max_backoff_sec=6.0, timeout_sec=20.0)
    )
    async def find_flights(origin: str, destination: str) -> str:
        """Find available flights between cities."""
        logger.info(f"[Travel Agent] Searching flights {origin} -> {destination}...")
        await asyncio.sleep(3)
        return f"Flights {origin}-{destination}: Economy from $180, Business from $650. 8 daily flights available."

    @integration.asyncflow_tool(
        retry=RetryConfig(max_attempts=3, base_backoff_sec=0.4, max_backoff_sec=6.0, timeout_sec=12.0)
    )
    async def get_travel_requirements(destination: str) -> str:
        """Get travel requirements and documentation needed."""
        logger.info(f"[Travel Agent] Checking requirements for {destination}...")
        await asyncio.sleep(1.5)
        return f"Travel to {destination}: Passport required. No visa needed. COVID restrictions lifted."

    # Local Agent Tools
    @integration.asyncflow_tool(
        retry=RetryConfig(max_attempts=2, base_backoff_sec=0.5, max_backoff_sec=4.0, timeout_sec=10.0)
    )
    async def find_attractions(city: str) -> str:
        """Find top attractions and activities in a city."""
        logger.info(f"[Local Agent] Finding attractions in {city}...")
        await asyncio.sleep(2.5)
        return f"Top attractions in {city}: Big Ben, London Eye, British Museum, Tower Bridge, Hyde Park."

    @integration.asyncflow_tool(
        retry=RetryConfig(max_attempts=2, base_backoff_sec=0.5, max_backoff_sec=4.0, timeout_sec=10.0)
    )
    async def get_local_tips(city: str) -> str:
        """Get local tips and cultural information."""
        logger.info(f"[Local Agent] Gathering local tips for {city}...")
        await asyncio.sleep(1.8)
        return f"Local tips for {city}: Use Oyster card for transport, tip 10-15%, pubs close early on Sundays."

    # --- Individual Agent Nodes ---

    @flow.function_task
    async def weather_agent(task: str, location: str) -> Dict[str, Any]:
        """Weather specialist agent with its own reasoning."""
        logger.info(f"[Weather Agent] Processing task: {task}")

        # Create specialized LLM for weather
        llm = ChatOpenAI(
            temperature=0.1,
            model="deepseek/deepseek-chat-v3-0324:free",
            openai_api_base="https://openrouter.ai/api/v1",
            openai_api_key=os.getenv("OPEN_ROUTER_API_KEY"),
            max_retries=3,
            timeout=30,
        )

        weather_tools = [get_detailed_weather, get_weather_alerts]
        llm_with_tools = llm.bind_tools(weather_tools)

        messages = [
            SystemMessage(content="You are a weather specialist. Provide comprehensive weather analysis."),
            HumanMessage(content=f"Analyze weather conditions for {location}. Task: {task}")
        ]

        # Agent reasoning and tool usage
        response = await llm_with_tools.ainvoke(messages)

        # If agent wants to use tools, execute them
        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_results = []
            for tool_call in response.tool_calls:
                if tool_call['name'] == 'get_detailed_weather':
                    result = await get_detailed_weather.ainvoke({"city": tool_call['args']['city']})
                    tool_results.append(result)
                elif tool_call['name'] == 'get_weather_alerts':
                    result = await get_weather_alerts.ainvoke({"city": tool_call['args']['city']})
                    tool_results.append(result)

            # Final synthesis
            final_messages = messages + [response, HumanMessage(content=f"Tool results: {tool_results}. Provide final weather analysis.")]
            final_response = await llm.ainvoke(final_messages)

            return {
                "agent": "weather",
                "result": final_response.content,
                "tools_used": [tc['name'] for tc in response.tool_calls]
            }

        return {
            "agent": "weather",
            "result": response.content,
            "tools_used": []
        }

    @flow.function_task
    async def travel_agent(task: str, origin: str, destination: str) -> Dict[str, Any]:
        """Travel specialist agent."""
        logger.info(f"[Travel Agent] Processing task: {task}")

        llm = ChatOpenAI(
            temperature=0.2,
            model="deepseek/deepseek-chat-v3-0324:free",
            openai_api_base="https://openrouter.ai/api/v1",
            openai_api_key=os.getenv("OPEN_ROUTER_API_KEY"),
            max_retries=3,
            timeout=30,
        )

        travel_tools = [find_flights, get_travel_requirements]
        llm_with_tools = llm.bind_tools(travel_tools)

        messages = [
            SystemMessage(content="You are a travel specialist. Help with flights, documentation, and travel logistics."),
            HumanMessage(content=f"Help plan travel from {origin} to {destination}. Task: {task}")
        ]

        response = await llm_with_tools.ainvoke(messages)

        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_results = []
            for tool_call in response.tool_calls:
                if tool_call['name'] == 'find_flights':
                    result = await find_flights.ainvoke({
                        "origin": tool_call['args']['origin'],
                        "destination": tool_call['args']['destination']
                    })
                    tool_results.append(result)
                elif tool_call['name'] == 'get_travel_requirements':
                    result = await get_travel_requirements.ainvoke({"destination": tool_call['args']['destination']})
                    tool_results.append(result)

            final_messages = messages + [response, HumanMessage(content=f"Tool results: {tool_results}. Provide final travel recommendations.")]
            final_response = await llm.ainvoke(final_messages)

            return {
                "agent": "travel",
                "result": final_response.content,
                "tools_used": [tc['name'] for tc in response.tool_calls]
            }

        return {
            "agent": "travel",
            "result": response.content,
            "tools_used": []
        }

    @flow.function_task
    async def local_agent(task: str, city: str) -> Dict[str, Any]:
        """Local information specialist agent."""
        logger.info(f"[Local Agent] Processing task: {task}")

        llm = ChatOpenAI(
            temperature=0.3,
            model="deepseek/deepseek-chat-v3-0324:free",
            openai_api_base="https://openrouter.ai/api/v1",
            openai_api_key=os.getenv("OPEN_ROUTER_API_KEY"),
            max_retries=3,
            timeout=30,
        )

        local_tools = [find_attractions, get_local_tips]
        llm_with_tools = llm.bind_tools(local_tools)

        messages = [
            SystemMessage(content="You are a local guide specialist. Provide insights about attractions, culture, and local tips."),
            HumanMessage(content=f"Provide local guidance for {city}. Task: {task}")
        ]

        response = await llm_with_tools.ainvoke(messages)

        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_results = []
            for tool_call in response.tool_calls:
                if tool_call['name'] == 'find_attractions':
                    result = await find_attractions.ainvoke({"city": tool_call['args']['city']})
                    tool_results.append(result)
                elif tool_call['name'] == 'get_local_tips':
                    result = await get_local_tips.ainvoke({"city": tool_call['args']['city']})
                    tool_results.append(result)

            final_messages = messages + [response, HumanMessage(content=f"Tool results: {tool_results}. Provide final local recommendations.")]
            final_response = await llm.ainvoke(final_messages)

            return {
                "agent": "local",
                "result": final_response.content,
                "tools_used": [tc['name'] for tc in response.tool_calls]
            }

        return {
            "agent": "local",
            "result": response.content,
            "tools_used": []
        }

    # --- AsyncFlow Block for Parallel Agent Execution ---
    @flow.block
    async def parallel_agent_workflow(origin: str, destination: str) -> Dict[str, Any]:
        """Execute multiple agents in parallel through AsyncFlow."""
        logger.info("[AsyncFlow Block] Starting parallel agent execution...")

        # Launch all agents in parallel
        weather_future = weather_agent(
            task="Analyze weather conditions and provide forecast",
            location=destination
        )

        travel_future = travel_agent(
            task="Find flights and travel requirements",
            origin=origin,
            destination=destination
        )

        local_future = local_agent(
            task="Recommend attractions and provide local tips",
            city=destination
        )

        # Wait for all agents to complete
        weather_result = await weather_future
        travel_result = await travel_future
        local_result = await local_future

        return {
            "weather": weather_result,
            "travel": travel_result,
            "local": local_result
        }

    # --- LangGraph Coordinator Node ---
    async def coordinator_node(state: MultiAgentState):
        """Coordinates the parallel agents and synthesizes results."""
        messages = state["messages"]
        last_message = messages[-1].content

        # Parse the user request
        if "paris" in last_message.lower() and "london" in last_message.lower():
            logger.info("[Coordinator] Dispatching parallel agents for Paris->London trip...")

            # Execute parallel agents through AsyncFlow
            agent_results = await parallel_agent_workflow("Paris", "London")

            # Synthesize results with master coordinator LLM
            coordinator_llm = ChatOpenAI(
                temperature=0.4,
                model="deepseek/deepseek-chat-v3-0324:free",
                openai_api_base="https://openrouter.ai/api/v1",
                openai_api_key=os.getenv("OPEN_ROUTER_API_KEY"),
                max_retries=3,
                timeout=45,
            )

            synthesis_prompt = f"""
            I have received reports from three specialist agents about a Paris to London trip:

            WEATHER AGENT: {agent_results['weather']['result']}
            TRAVEL AGENT: {agent_results['travel']['result']}
            LOCAL AGENT: {agent_results['local']['result']}

            Please synthesize this information into a comprehensive travel plan that addresses the user's request: "{last_message}"
            """

            final_response = await coordinator_llm.ainvoke([HumanMessage(content=synthesis_prompt)])

            return {
                "messages": [final_response],
                "agent_results": agent_results
            }

        # Fallback for other requests
        simple_llm = ChatOpenAI(
            temperature=0.3,
            model="deepseek/deepseek-chat-v3-0324:free",
            openai_api_base="https://openrouter.ai/api/v1",
            openai_api_key=os.getenv("OPEN_ROUTER_API_KEY"),
            max_retries=2,
            timeout=30,
        )

        response = await simple_llm.ainvoke(messages)
        return {"messages": [response]}

    # --- Build LangGraph Workflow ---
    workflow = StateGraph(MultiAgentState)
    workflow.add_node("coordinator", coordinator_node)
    workflow.set_entry_point("coordinator")
    workflow.add_edge("coordinator", END)

    # Enable checkpointing so the graph can resume gracefully
    checkpointer = MemorySaver()
    app = workflow.compile(checkpointer=checkpointer)

    # --- Test Parallel Agents ---
    logger.info("🚀 Testing parallel agent execution...")
    logger.info("=" * 60)

    user_message = """I'm planning a trip from Paris to London next week. I need:
    1. Weather forecast and what to pack
    2. Flight options and travel requirements
    3. Must-see attractions and local customs

    Please coordinate with your specialist agents to give me a complete plan."""

    logger.info(f"User: {user_message}")
    logger.info("\n🤖 Coordinator dispatching parallel agents...")

    start_time = asyncio.get_event_loop().time()

    # Run parallel agents
    final_state = await app.ainvoke({
        "messages": [HumanMessage(content=user_message)],
        "agent_results": {},
        "task_assignments": {}
    })

    end_time = asyncio.get_event_loop().time()

    # Show results
    final_response = final_state["messages"][-1]
    agent_results = final_state.get("agent_results", {})

    logger.info(f"\n✅ Final Coordinated Response: {final_response.content}")
    logger.info(f"⏱️ Total execution time: {end_time - start_time:.2f} seconds")

    if agent_results:
        logger.info("\n📊 Agent Execution Summary:")
        for agent_name, result in agent_results.items():
            logger.info(f"  {agent_name.upper()} Agent: Used tools {result.get('tools_used', [])}")

    logger.info("\n💡 Note: All specialist agents ran in parallel through AsyncFlow!")
    logger.info("   Sequential execution would take ~7+ seconds")
    logger.info("   Parallel execution completed in ~3-4 seconds")

    await flow.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
