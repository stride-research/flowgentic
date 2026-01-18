import json
import operator
import logging
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field
from typing import Literal, Optional, Annotated, List, Dict, Callable
from langgraph.types import Command, Send
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.prebuilt import create_react_agent
import ast


# STATES


# Pydantic model for structured routing output
class RoutingDecision(BaseModel):
	"""Schema for LLM routing decisions."""

	routing_decision: List[str] = Field(
		description="List of agent names to route the query to"
	)
	routing_rationale: str = Field(
		description="Explanation for why these agents were selected"
	)


# ROUTER FACTORY
def create_llm_router(agents_responsibilities: str, model: BaseChatModel) -> Callable:
	"""
	Factory function to create an LLM router node.

	Args:
	    agents_responsibilities: Description of available agents and their responsibilities
	    model: LangChain chat model to use for routing decisions

	Returns:
	    Async function that can be decorated with @agents_manager.execution_wrappers.asyncflow

	"""

	routing_prompt_template = """
Based on the user's query, decide which agent(s) should handle it:

{agents_responsibilities}

User query: "{query}"

Notes:
	- You can select multiple agents to work in parallel
	- You can select just one agent if that's most appropriate
	- Explain your reasoning for the selection
"""

	async def llm_router(state: BaseModel) -> dict:
		"""LLM decides which agent(s) to route to based on user query."""

		logging.info(f"🧠 LLM Router: Analyzing query '{state.query}'")

		# Format the routing prompt with the actual query
		formatted_prompt = routing_prompt_template.format(
			query=state.query, agents_responsibilities=agents_responsibilities
		)

		# Create input messages
		input_messages = [
			SystemMessage(
				content="You are a routing assistant. Analyze queries and decide which agent(s) should handle them."
			),
			HumanMessage(content=formatted_prompt),
		]

		# Use structured output for guaranteed JSON parsing
		structured_llm = model.with_structured_output(RoutingDecision)

		# Get structured response directly as Pydantic model
		routing_decision_obj: RoutingDecision = await structured_llm.ainvoke(
			input_messages
		)

		# Extract routing decision from structured response
		routing_decision = routing_decision_obj.routing_decision
		routing_rationale = routing_decision_obj.routing_rationale

		# Response validation
		if not routing_decision:
			raise ValueError("Empty routing decision from LLM")

		logging.info(
			f"✅ Router decided: {routing_decision} - Rationale: {routing_rationale}"
		)

		# Update state
		state.routing_decision = routing_decision
		state.routing_rationale = routing_rationale

		# Create AIMessage to represent the routing decision with metadata
		from langchain_core.messages import AIMessage

		ai_message = AIMessage(
			content=f"Routing Decision: {routing_decision}\nRationale: {routing_rationale}"
		)

		# Add messages to state if it has a messages field
		if hasattr(state, "messages"):
			state.messages.extend(input_messages + [ai_message])

		return state

	return llm_router


# FAN-OUT HELPER
def supervisor_fan_out(state: BaseModel) -> Command[Literal["agent_A", "agent_B"]]:
	"""
	Fan out based on LLM routing decision.

	Args:
	    state: Current graph state with routing_decision populated

	Returns:
	    List of Send objects to route to selected agents
	"""
	decision = state.routing_decision or []
	return [Send(agent, state) for agent in decision]  # Send API for concurrency
