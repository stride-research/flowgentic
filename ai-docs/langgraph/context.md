Context¶
Context engineering is the practice of building dynamic systems that provide the right information and tools, in the right format, so that an AI application can accomplish a task. Context can be characterized along two key dimensions:

By mutability:
Static context: Immutable data that doesn't change during execution (e.g., user metadata, database connections, tools)
Dynamic context: Mutable data that evolves as the application runs (e.g., conversation history, intermediate results, tool call observations)
By lifetime:
Runtime context: Data scoped to a single run or invocation
Cross-conversation context: Data that persists across multiple conversations or sessions
Runtime context vs LLM context

Runtime context refers to local context: data and dependencies your code needs to run. It does not refer to:

The LLM context, which is the data passed into the LLM's prompt.
The "context window", which is the maximum number of tokens that can be passed to the LLM.
Runtime context can be used to optimize the LLM context. For example, you can use user metadata in the runtime context to fetch user preferences and feed them into the context window.

LangGraph provides three ways to manage context, which combines the mutability and lifetime dimensions:

Context type Description Mutability Lifetime Access method
Static runtime context User metadata, tools, db connections passed at startup Static Single run context argument to invoke/stream
Dynamic runtime context (state) Mutable data that evolves during a single run Dynamic Single run LangGraph state object
Dynamic cross-conversation context (store) Persistent data shared across conversations Dynamic Cross-conversation LangGraph store
Static runtime context¶
Static runtime context represents immutable data like user metadata, tools, and database connections that are passed to an application at the start of a run via the context argument to invoke/stream. This data does not change during execution.

New in LangGraph v0.6: context replaces config['configurable']

Runtime context is now passed to the context argument of invoke/stream, which replaces the previous pattern of passing application configuration to config['configurable'].

@dataclass
class ContextSchema:
user_name: str

graph.invoke(
{"messages": [{"role": "user", "content": "hi!"}]},
context={"user_name": "John Smith"}
)

Agent prompt
Workflow node
In a tool

from langchain_core.messages import AnyMessage
from langgraph.runtime import get_runtime
from langgraph.prebuilt.chat_agent_executor import AgentState
from langgraph.prebuilt import create_react_agent

def prompt(state: AgentState) -> list[AnyMessage]:
runtime = get_runtime(ContextSchema)
system_msg = f"You are a helpful assistant. Address the user as {runtime.context.user_name}."
return [{"role": "system", "content": system_msg}] + state["messages"]

agent = create_react_agent(
model="anthropic:claude-3-7-sonnet-latest",
tools=[get_weather],
prompt=prompt,
context_schema=ContextSchema
)

agent.invoke(
{"messages": [{"role": "user", "content": "what is the weather in sf"}]},
context={"user_name": "John Smith"}
)
See Agents for details.

Tip

The Runtime object can be used to access static context and other utilities like the active store and stream writer. See the Runtime documentation for details.

Dynamic runtime context (state)¶
Dynamic runtime context represents mutable data that can evolve during a single run and is managed through the LangGraph state object. This includes conversation history, intermediate results, and values derived from tools or LLM outputs. In LangGraph, the state object acts as short-term memory during a run.

In an agent
In a workflow

from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage
from langgraph.graph import StateGraph

class CustomState(TypedDict):
messages: list[AnyMessage]
extra_field: int

def node(state: CustomState):
messages = state["messages"]
...
return {
"extra_field": state["extra_field"] + 1
}

builder = StateGraph(State)
builder.add_node(node)
builder.set_entry_point("node")
graph = builder.compile()

Turning on memory

Please see the memory guide for more details on how to enable memory. This is a powerful feature that allows you to persist the agent's state across multiple invocations. Otherwise, the state is scoped only to a single run.

Dynamic cross-conversation context (store)¶
Dynamic cross-conversation context represents persistent, mutable data that spans across multiple conversations or sessions and is managed through the LangGraph store. This includes user profiles, preferences, and historical interactions. The LangGraph store acts as long-term memory across multiple runs. This can be used to read or update persistent facts (e.g., user profiles, preferences, prior interactions).

For more information, see the Memory guide.
