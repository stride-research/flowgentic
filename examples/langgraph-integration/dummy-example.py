from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END, add_messages
from typing import Annotated
from pydantic import BaseModel, Field

from flowgentic.llm_providers import ChatLLMProvider

from dotenv import load_dotenv

load_dotenv()


# Simple schema for boolean decision
class Decision(BaseModel):
	decision: bool = Field(description="True or false decision")
	reasoning: str = Field(description="Brief explanation of the decision")


# State
class AgentState(BaseModel):
	messages: Annotated[list, add_messages]
	decision: Decision or None


# Create LLM with structured output
llm = ChatLLMProvider(provider="OpenRouter", model="google/gemini-2.5-flash")
structured_llm = llm.with_structured_output(Decision)


def decision_node(state: AgentState):
	# Your agent logic here (tool calls, reasoning, etc.)
	messages = state.messages

	# After all tool usage and reasoning, make final structured decision
	result = structured_llm.invoke(
		[
			{
				"role": "system",
				"content": "Based on the conversation, make a final decision.",
			},
			*messages,
		]
	)

	return {"decision": result}


# Build graph
graph = StateGraph(AgentState)
graph.add_node("decision", decision_node)
graph.set_entry_point("decision")
graph.add_edge("decision", END)
