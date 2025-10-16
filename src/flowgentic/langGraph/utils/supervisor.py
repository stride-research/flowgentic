import operator
import logging
from pydantic import BaseModel, Field
from typing import Literal, Optional, Annotated, List, Dict, Callable
from langgraph.types import Command, Send
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.prebuilt import create_react_agent
import ast


# STATES


# ROUTER FACTORY
def create_llm_router(routing_prompt_template: str, model) -> Callable:
	"""
	Factory function to create an LLM router node.

	Args:
	    routing_prompt_template: Template string with {query} placeholder for the routing prompt
	    model: LangChain chat model to use for routing decisions

	Returns:
	    Async function that can be decorated with @agents_manager.execution_wrappers.asyncflow

	Example:
	    ```python
	    router_prompt = '''
	    Based on the user's query, decide which agent(s) should handle it:
	    - agent_a: Handles data processing
	    - agent_b: Handles Q&A

	    User query: "{query}"

	    Respond with a list like ["agent_a"] or ["agent_a", "agent_b"]
	    '''

	    llm_router = agents_manager.execution_wrappers.asyncflow(
	        create_llm_router(router_prompt, model),
	        flow_type=AsyncFlowType.EXECUTION_BLOCK,
	    )
	    ```
	"""

	async def llm_router(state: BaseModel) -> dict:
		"""LLM decides which agent(s) to route to based on user query."""
		logging.info(f"🧠 LLM Router: Analyzing query '{state.query}'")

		# Create a simple react agent to make routing decision
		router_agent = create_react_agent(
			model=model,
			tools=[],  # No tools needed for simple routing
		)

		# Format the routing prompt with the actual query
		formatted_prompt = routing_prompt_template.format(query=state.query)

		result = await router_agent.ainvoke(
			{
				"messages": [
					SystemMessage(
						content="You are a routing assistant. Analyze queries and decide which agent should handle them."
					),
					HumanMessage(content=formatted_prompt),
				]
			}
		)

		# Extract routing decision from LLM response
		decision = result["messages"][
			-1
		].content.strip()  # Extract decision + decision rationale

		# Response validation
		if not decision:
			raise ValueError("Empty routing decision from LLM")
		else:
			parsed_list: List[str] = ast.literal_eval(decision)
			decision = [item.strip() for item in parsed_list]

		logging.info(f"✅ Router decided: {decision}")
		state.routing_decision = decision
		return state

	return llm_router


# FAN-OUT HELPER
def supervisor_fan_out(state: BaseModel) -> Command[Literal["agentA", "agentB"]]:
	"""
	Fan out based on LLM routing decision.

	Args:
	    state: Current graph state with routing_decision populated

	Returns:
	    List of Send objects to route to selected agents
	"""
	decision = state.routing_decision or []
	return [Send(agent, state) for agent in decision]
