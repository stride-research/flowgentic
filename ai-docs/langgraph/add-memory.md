Add and manage memory¶
AI applications need memory to share context across multiple interactions. In LangGraph, you can add two types of memory:

Add short-term memory as a part of your agent's state to enable multi-turn conversations.
Add long-term memory to store user-specific or application-level data across sessions.
Add short-term memory¶
Short-term memory (thread-level persistence) enables agents to track multi-turn conversations. To add short-term memory:

API Reference: InMemorySaver | StateGraph

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph

checkpointer = InMemorySaver()

builder = StateGraph(...)
graph = builder.compile(checkpointer=checkpointer)

graph.invoke(
{"messages": [{"role": "user", "content": "hi! i am Bob"}]},
{"configurable": {"thread_id": "1"}},
)
Use in production¶
In production, use a checkpointer backed by a database:

API Reference: PostgresSaver

from langgraph.checkpoint.postgres import PostgresSaver

DB_URI = "postgresql://postgres:postgres@localhost:5442/postgres?sslmode=disable"
with PostgresSaver.from_conn_string(DB_URI) as checkpointer:
builder = StateGraph(...)
graph = builder.compile(checkpointer=checkpointer)
Example: using Postgres checkpointer

pip install -U "psycopg[binary,pool]" langgraph langgraph-checkpoint-postgres
Setup

You need to call checkpointer.setup() the first time you're using Postgres checkpointer

Sync
Async

from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, MessagesState, START
from langgraph.checkpoint.postgres import PostgresSaver

model = init_chat_model(model="anthropic:claude-3-5-haiku-latest")

DB_URI = "postgresql://postgres:postgres@localhost:5442/postgres?sslmode=disable"
with PostgresSaver.from_conn_string(DB_URI) as checkpointer: # checkpointer.setup()

    def call_model(state: MessagesState):
        response = model.invoke(state["messages"])
        return {"messages": response}

    builder = StateGraph(MessagesState)
    builder.add_node(call_model)
    builder.add_edge(START, "call_model")

    graph = builder.compile(checkpointer=checkpointer)

    config = {
        "configurable": {
            "thread_id": "1"
        }
    }

    for chunk in graph.stream(
        {"messages": [{"role": "user", "content": "hi! I'm bob"}]},
        config,
        stream_mode="values"
    ):
        chunk["messages"][-1].pretty_print()

    for chunk in graph.stream(
        {"messages": [{"role": "user", "content": "what's my name?"}]},
        config,
        stream_mode="values"
    ):
        chunk["messages"][-1].pretty_print()

Example: using MongoDB checkpointer

pip install -U pymongo langgraph langgraph-checkpoint-mongodb
Setup

To use the MongoDB checkpointer, you will need a MongoDB cluster. Follow this guide to create a cluster if you don't already have one.

Sync
Async

from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, MessagesState, START
from langgraph.checkpoint.mongodb import MongoDBSaver

model = init_chat_model(model="anthropic:claude-3-5-haiku-latest")

DB_URI = "localhost:27017"
with MongoDBSaver.from_conn_string(DB_URI) as checkpointer:

    def call_model(state: MessagesState):
        response = model.invoke(state["messages"])
        return {"messages": response}

    builder = StateGraph(MessagesState)
    builder.add_node(call_model)
    builder.add_edge(START, "call_model")

    graph = builder.compile(checkpointer=checkpointer)

    config = {
        "configurable": {
            "thread_id": "1"
        }
    }

    for chunk in graph.stream(
        {"messages": [{"role": "user", "content": "hi! I'm bob"}]},
        config,
        stream_mode="values"
    ):
        chunk["messages"][-1].pretty_print()

    for chunk in graph.stream(
        {"messages": [{"role": "user", "content": "what's my name?"}]},
        config,
        stream_mode="values"
    ):
        chunk["messages"][-1].pretty_print()

Example: using Redis checkpointer

pip install -U langgraph langgraph-checkpoint-redis
Setup

You need to call checkpointer.setup() the first time you're using Redis checkpointer

Sync
Async

from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, MessagesState, START
from langgraph.checkpoint.redis import RedisSaver

model = init_chat_model(model="anthropic:claude-3-5-haiku-latest")

DB_URI = "redis://localhost:6379"
with RedisSaver.from_conn_string(DB_URI) as checkpointer: # checkpointer.setup()

    def call_model(state: MessagesState):
        response = model.invoke(state["messages"])
        return {"messages": response}

    builder = StateGraph(MessagesState)
    builder.add_node(call_model)
    builder.add_edge(START, "call_model")

    graph = builder.compile(checkpointer=checkpointer)

    config = {
        "configurable": {
            "thread_id": "1"
        }
    }

    for chunk in graph.stream(
        {"messages": [{"role": "user", "content": "hi! I'm bob"}]},
        config,
        stream_mode="values"
    ):
        chunk["messages"][-1].pretty_print()

    for chunk in graph.stream(
        {"messages": [{"role": "user", "content": "what's my name?"}]},
        config,
        stream_mode="values"
    ):
        chunk["messages"][-1].pretty_print()

Use in subgraphs¶
If your graph contains subgraphs, you only need to provide the checkpointer when compiling the parent graph. LangGraph will automatically propagate the checkpointer to the child subgraphs.

API Reference: START | StateGraph | InMemorySaver

from langgraph.graph import START, StateGraph
from langgraph.checkpoint.memory import InMemorySaver
from typing import TypedDict

class State(TypedDict):
foo: str

# Subgraph

def subgraph_node_1(state: State):
return {"foo": state["foo"] + "bar"}

subgraph_builder = StateGraph(State)
subgraph_builder.add_node(subgraph_node_1)
subgraph_builder.add_edge(START, "subgraph_node_1")
subgraph = subgraph_builder.compile()

# Parent graph

builder = StateGraph(State)
builder.add_node("node_1", subgraph)
builder.add_edge(START, "node_1")

checkpointer = InMemorySaver()
graph = builder.compile(checkpointer=checkpointer)
If you want the subgraph to have its own memory, you can compile it with the appropriate checkpointer option. This is useful in multi-agent systems, if you want agents to keep track of their internal message histories.

subgraph_builder = StateGraph(...)
subgraph = subgraph_builder.compile(checkpointer=True)
Read short-term memory in tools¶
LangGraph allows agents to access their short-term memory (state) inside the tools.

API Reference: InjectedState | create_react_agent

from typing import Annotated
from langgraph.prebuilt import InjectedState, create_react_agent

class CustomState(AgentState):
user_id: str

def get_user_info(
state: Annotated[CustomState, InjectedState]
) -> str:
"""Look up user info."""
user_id = state["user_id"]
return "User is John Smith" if user_id == "user_123" else "Unknown user"

agent = create_react_agent(
model="anthropic:claude-3-7-sonnet-latest",
tools=[get_user_info],
state_schema=CustomState,
)

agent.invoke({
"messages": "look up user information",
"user_id": "user_123"
})
See the Context guide for more information.

Write short-term memory from tools¶
To modify the agent's short-term memory (state) during execution, you can return state updates directly from the tools. This is useful for persisting intermediate results or making information accessible to subsequent tools or prompts.

API Reference: InjectedToolCallId | RunnableConfig | ToolMessage | InjectedState | create_react_agent | AgentState | Command

from typing import Annotated
from langchain_core.tools import InjectedToolCallId
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import ToolMessage
from langgraph.prebuilt import InjectedState, create_react_agent
from langgraph.prebuilt.chat_agent_executor import AgentState
from langgraph.types import Command

class CustomState(AgentState):
user_name: str

def update_user_info(
tool_call_id: Annotated[str, InjectedToolCallId],
config: RunnableConfig
) -> Command:
"""Look up and update user info."""
user_id = config["configurable"].get("user_id")
name = "John Smith" if user_id == "user_123" else "Unknown user"
return Command(update={
"user_name": name, # update the message history
"messages": [
ToolMessage(
"Successfully looked up user information",
tool_call_id=tool_call_id
)
]
})

def greet(
state: Annotated[CustomState, InjectedState]
) -> str:
"""Use this to greet the user once you found their info."""
user_name = state["user_name"]
return f"Hello {user_name}!"

agent = create_react_agent(
model="anthropic:claude-3-7-sonnet-latest",
tools=[update_user_info, greet],
state_schema=CustomState
)

agent.invoke(
{"messages": [{"role": "user", "content": "greet the user"}]},
config={"configurable": {"user_id": "user_123"}}
)
Add long-term memory¶
Use long-term memory to store user-specific or application-specific data across conversations.

API Reference: StateGraph

from langgraph.store.memory import InMemoryStore
from langgraph.graph import StateGraph

store = InMemoryStore()

builder = StateGraph(...)
graph = builder.compile(store=store)
Use in production¶
In production, use a store backed by a database:

from langgraph.store.postgres import PostgresStore

DB_URI = "postgresql://postgres:postgres@localhost:5442/postgres?sslmode=disable"
with PostgresStore.from_conn_string(DB_URI) as store:
builder = StateGraph(...)
graph = builder.compile(store=store)
Example: using Postgres store
Example: using Redis store
Read long-term memory in tools¶
A tool the agent can use to look up user information

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_store
from langgraph.prebuilt import create_react_agent
from langgraph.store.memory import InMemoryStore

store = InMemoryStore()

store.put(  
 ("users",),  
 "user_123",  
 {
"name": "John Smith",
"language": "English",
}
)

def get_user_info(config: RunnableConfig) -> str:
"""Look up user info.""" # Same as that provided to `create_react_agent`
store = get_store()
user_id = config["configurable"].get("user_id")
user_info = store.get(("users",), user_id)
return str(user_info.value) if user_info else "Unknown user"

agent = create_react_agent(
model="anthropic:claude-3-7-sonnet-latest",
tools=[get_user_info],
store=store
)

# Run the agent

agent.invoke(
{"messages": [{"role": "user", "content": "look up user information"}]},
config={"configurable": {"user_id": "user_123"}}
)
Write long-term memory from tools¶
Example of a tool that updates user information

from typing_extensions import TypedDict

from langgraph.config import get_store
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent
from langgraph.store.memory import InMemoryStore

store = InMemoryStore()

class UserInfo(TypedDict):
name: str

def save_user_info(user_info: UserInfo, config: RunnableConfig) -> str:
"""Save user info.""" # Same as that provided to `create_react_agent`
store = get_store()
user_id = config["configurable"].get("user_id")
store.put(("users",), user_id, user_info)
return "Successfully saved user info."

agent = create_react_agent(
model="anthropic:claude-3-7-sonnet-latest",
tools=[save_user_info],
store=store
)

# Run the agent

agent.invoke(
{"messages": [{"role": "user", "content": "My name is John Smith"}]},
config={"configurable": {"user_id": "user_123"}}
)

# You can access the store directly to get the value

store.get(("users",), "user_123").value
Use semantic search¶
Enable semantic search in your graph's memory store to let graph agents search for items in the store by semantic similarity.

API Reference: init_embeddings

from langchain.embeddings import init_embeddings
from langgraph.store.memory import InMemoryStore

# Create store with semantic search enabled

embeddings = init_embeddings("openai:text-embedding-3-small")
store = InMemoryStore(
index={
"embed": embeddings,
"dims": 1536,
}
)

store.put(("user_123", "memories"), "1", {"text": "I love pizza"})
store.put(("user_123", "memories"), "2", {"text": "I am a plumber"})

items = store.search(
("user_123", "memories"), query="I'm hungry", limit=1
)
Long-term memory with semantic search

from typing import Optional

from langchain.embeddings import init_embeddings
from langchain.chat_models import init_chat_model
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.graph import START, MessagesState, StateGraph

llm = init_chat_model("openai:gpt-4o-mini")

# Create store with semantic search enabled

embeddings = init_embeddings("openai:text-embedding-3-small")
store = InMemoryStore(
index={
"embed": embeddings,
"dims": 1536,
}
)

store.put(("user_123", "memories"), "1", {"text": "I love pizza"})
store.put(("user_123", "memories"), "2", {"text": "I am a plumber"})

def chat(state, *, store: BaseStore): # Search based on user's last message
items = store.search(
("user_123", "memories"), query=state["messages"][-1].content, limit=2
)
memories = "\n".join(item.value["text"] for item in items)
memories = f"## Memories of user\n{memories}" if memories else ""
response = llm.invoke(
[
{"role": "system", "content": f"You are a helpful assistant.\n{memories}"},
*state["messages"],
]
)
return {"messages": [response]}

builder = StateGraph(MessagesState)
builder.add_node(chat)
builder.add_edge(START, "chat")
graph = builder.compile(store=store)

for message, metadata in graph.stream(
input={"messages": [{"role": "user", "content": "I'm hungry"}]},
stream_mode="messages",
):
print(message.content, end="")
See this guide for more information on how to use semantic search with LangGraph memory store.

Manage short-term memory¶
With short-term memory enabled, long conversations can exceed the LLM's context window. Common solutions are:

Trim messages: Remove first or last N messages (before calling LLM)
Delete messages from LangGraph state permanently
Summarize messages: Summarize earlier messages in the history and replace them with a summary
Manage checkpoints to store and retrieve message history
Custom strategies (e.g., message filtering, etc.)
This allows the agent to keep track of the conversation without exceeding the LLM's context window.

Trim messages¶
Most LLMs have a maximum supported context window (denominated in tokens). One way to decide when to truncate messages is to count the tokens in the message history and truncate whenever it approaches that limit. If you're using LangChain, you can use the trim messages utility and specify the number of tokens to keep from the list, as well as the strategy (e.g., keep the last maxTokens) to use for handling the boundary.

In an agent
In a workflow
To trim message history, use the trim_messages function:

from langchain_core.messages.utils import (
trim_messages,
count_tokens_approximately
)

def call_model(state: MessagesState):
messages = trim_messages(
state["messages"],
strategy="last",
token_counter=count_tokens_approximately,
max_tokens=128,
start_on="human",
end_on=("human", "tool"),
)
response = model.invoke(messages)
return {"messages": [response]}

builder = StateGraph(MessagesState)
builder.add_node(call_model)
...

Full example: trim messages

from langchain_core.messages.utils import (
trim_messages,
count_tokens_approximately
)
from langchain.chat_models import init_chat_model
from langgraph.graph import StateGraph, START, MessagesState

model = init_chat_model("anthropic:claude-3-7-sonnet-latest")
summarization_model = model.bind(max_tokens=128)

def call_model(state: MessagesState):
messages = trim_messages(
state["messages"],
strategy="last",
token_counter=count_tokens_approximately,
max_tokens=128,
start_on="human",
end_on=("human", "tool"),
)
response = model.invoke(messages)
return {"messages": [response]}

checkpointer = InMemorySaver()
builder = StateGraph(MessagesState)
builder.add_node(call_model)
builder.add_edge(START, "call_model")
graph = builder.compile(checkpointer=checkpointer)

config = {"configurable": {"thread_id": "1"}}
graph.invoke({"messages": "hi, my name is bob"}, config)
graph.invoke({"messages": "write a short poem about cats"}, config)
graph.invoke({"messages": "now do the same but for dogs"}, config)
final_response = graph.invoke({"messages": "what's my name?"}, config)

final_response["messages"][-1].pretty_print()

================================== Ai Message ==================================

Your name is Bob, as you mentioned when you first introduced yourself.
Delete messages¶
You can delete messages from the graph state to manage the message history. This is useful when you want to remove specific messages or clear the entire message history.

To delete messages from the graph state, you can use the RemoveMessage. For RemoveMessage to work, you need to use a state key with add_messages reducer, like MessagesState.

To remove specific messages:

API Reference: RemoveMessage

from langchain_core.messages import RemoveMessage

def delete_messages(state):
messages = state["messages"]
if len(messages) > 2: # remove the earliest two messages
return {"messages": [RemoveMessage(id=m.id) for m in messages[:2]]}
To remove all messages:

from langgraph.graph.message import REMOVE_ALL_MESSAGES

def delete_messages(state):
return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)]}
Warning

When deleting messages, make sure that the resulting message history is valid. Check the limitations of the LLM provider you're using. For example:

some providers expect message history to start with a user message
most providers require assistant messages with tool calls to be followed by corresponding tool result messages.
Full example: delete messages

from langchain_core.messages import RemoveMessage

def delete_messages(state):
messages = state["messages"]
if len(messages) > 2: # remove the earliest two messages
return {"messages": [RemoveMessage(id=m.id) for m in messages[:2]]}

def call_model(state: MessagesState):
response = model.invoke(state["messages"])
return {"messages": response}

builder = StateGraph(MessagesState)
builder.add_sequence([call_model, delete_messages])
builder.add_edge(START, "call_model")

checkpointer = InMemorySaver()
app = builder.compile(checkpointer=checkpointer)

for event in app.stream(
{"messages": [{"role": "user", "content": "hi! I'm bob"}]},
config,
stream_mode="values"
):
print([(message.type, message.content) for message in event["messages"]])

for event in app.stream(
{"messages": [{"role": "user", "content": "what's my name?"}]},
config,
stream_mode="values"
):
print([(message.type, message.content) for message in event["messages"]])

[('human', "hi! I'm bob")]
[('human', "hi! I'm bob"), ('ai', 'Hi Bob! How are you doing today? Is there anything I can help you with?')]
[('human', "hi! I'm bob"), ('ai', 'Hi Bob! How are you doing today? Is there anything I can help you with?'), ('human', "what's my name?")]
[('human', "hi! I'm bob"), ('ai', 'Hi Bob! How are you doing today? Is there anything I can help you with?'), ('human', "what's my name?"), ('ai', 'Your name is Bob.')]
[('human', "what's my name?"), ('ai', 'Your name is Bob.')]
Summarize messages¶
The problem with trimming or removing messages, as shown above, is that you may lose information from culling of the message queue. Because of this, some applications benefit from a more sophisticated approach of summarizing the message history using a chat model.

In an agent
In a workflow
Prompting and orchestration logic can be used to summarize the message history. For example, in LangGraph you can extend the MessagesState to include a summary key:

from langgraph.graph import MessagesState
class State(MessagesState):
summary: str
Then, you can generate a summary of the chat history, using any existing summary as context for the next summary. This summarize_conversation node can be called after some number of messages have accumulated in the messages state key.

def summarize_conversation(state: State):

    # First, we get any existing summary
    summary = state.get("summary", "")

    # Create our summarization prompt
    if summary:

        # A summary already exists
        summary_message = (
            f"This is a summary of the conversation to date: {summary}\n\n"
            "Extend the summary by taking into account the new messages above:"
        )

    else:
        summary_message = "Create a summary of the conversation above:"

    # Add prompt to our history
    messages = state["messages"] + [HumanMessage(content=summary_message)]
    response = model.invoke(messages)

    # Delete all but the 2 most recent messages
    delete_messages = [RemoveMessage(id=m.id) for m in state["messages"][:-2]]
    return {"summary": response.content, "messages": delete_messages}

Full example: summarize messages

from typing import Any, TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.messages import AnyMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.graph import StateGraph, START, MessagesState
from langgraph.checkpoint.memory import InMemorySaver
from langmem.short_term import SummarizationNode, RunningSummary

model = init_chat_model("anthropic:claude-3-7-sonnet-latest")
summarization_model = model.bind(max_tokens=128)

class State(MessagesState):
context: dict[str, RunningSummary]

class LLMInputState(TypedDict):  
 summarized_messages: list[AnyMessage]
context: dict[str, RunningSummary]

summarization_node = SummarizationNode(
token_counter=count_tokens_approximately,
model=summarization_model,
max_tokens=256,
max_tokens_before_summary=256,
max_summary_tokens=128,
)

def call_model(state: LLMInputState):  
 response = model.invoke(state["summarized_messages"])
return {"messages": [response]}

checkpointer = InMemorySaver()
builder = StateGraph(State)
builder.add_node(call_model)
builder.add_node("summarize", summarization_node)
builder.add_edge(START, "summarize")
builder.add_edge("summarize", "call_model")
graph = builder.compile(checkpointer=checkpointer)

# Invoke the graph

config = {"configurable": {"thread_id": "1"}}
graph.invoke({"messages": "hi, my name is bob"}, config)
graph.invoke({"messages": "write a short poem about cats"}, config)
graph.invoke({"messages": "now do the same but for dogs"}, config)
final_response = graph.invoke({"messages": "what's my name?"}, config)

final_response["messages"][-1].pretty_print()
print("\nSummary:", final_response["context"]["running_summary"].summary)

================================== Ai Message ==================================

From our conversation, I can see that you introduced yourself as Bob. That's the name you shared with me when we began talking.

Summary: In this conversation, I was introduced to Bob, who then asked me to write a poem about cats. I composed a poem titled "The Mystery of Cats" that captured cats' graceful movements, independent nature, and their special relationship with humans. Bob then requested a similar poem about dogs, so I wrote "The Joy of Dogs," which highlighted dogs' loyalty, enthusiasm, and loving companionship. Both poems were written in a similar style but emphasized the distinct characteristics that make each pet special.
Manage checkpoints¶
You can view and delete the information stored by the checkpointer.

View thread state (checkpoint)¶

Graph/Functional API
Checkpointer API

config = {
"configurable": {
"thread_id": "1", # optionally provide an ID for a specific checkpoint, # otherwise the latest checkpoint is shown # "checkpoint_id": "1f029ca3-1f5b-6704-8004-820c16b69a5a"

    }

}
graph.get_state(config)

StateSnapshot(
values={'messages': [HumanMessage(content="hi! I'm bob"), AIMessage(content='Hi Bob! How are you doing today?), HumanMessage(content="what's my name?"), AIMessage(content='Your name is Bob.')]}, next=(),
config={'configurable': {'thread_id': '1', 'checkpoint_ns': '', 'checkpoint_id': '1f029ca3-1f5b-6704-8004-820c16b69a5a'}},
metadata={
'source': 'loop',
'writes': {'call_model': {'messages': AIMessage(content='Your name is Bob.')}},
'step': 4,
'parents': {},
'thread_id': '1'
},
created_at='2025-05-05T16:01:24.680462+00:00',
parent_config={'configurable': {'thread_id': '1', 'checkpoint_ns': '', 'checkpoint_id': '1f029ca3-1790-6b0a-8003-baf965b6a38f'}},
tasks=(),
interrupts=()
)

View the history of the thread (checkpoints)¶

Graph/Functional API
Checkpointer API

config = {
"configurable": {
"thread_id": "1"
}
}
list(graph.get_state_history(config))

[
StateSnapshot(
values={'messages': [HumanMessage(content="hi! I'm bob"), AIMessage(content='Hi Bob! How are you doing today? Is there anything I can help you with?'), HumanMessage(content="what's my name?"), AIMessage(content='Your name is Bob.')]},
next=(),
config={'configurable': {'thread_id': '1', 'checkpoint_ns': '', 'checkpoint_id': '1f029ca3-1f5b-6704-8004-820c16b69a5a'}},
metadata={'source': 'loop', 'writes': {'call_model': {'messages': AIMessage(content='Your name is Bob.')}}, 'step': 4, 'parents': {}, 'thread_id': '1'},
created_at='2025-05-05T16:01:24.680462+00:00',
parent_config={'configurable': {'thread_id': '1', 'checkpoint_ns': '', 'checkpoint_id': '1f029ca3-1790-6b0a-8003-baf965b6a38f'}},
tasks=(),
interrupts=()
),
StateSnapshot(
values={'messages': [HumanMessage(content="hi! I'm bob"), AIMessage(content='Hi Bob! How are you doing today? Is there anything I can help you with?'), HumanMessage(content="what's my name?")]},
next=('call_model',),
config={'configurable': {'thread_id': '1', 'checkpoint_ns': '', 'checkpoint_id': '1f029ca3-1790-6b0a-8003-baf965b6a38f'}},
metadata={'source': 'loop', 'writes': None, 'step': 3, 'parents': {}, 'thread_id': '1'},
created_at='2025-05-05T16:01:23.863421+00:00',
parent_config={...}
tasks=(PregelTask(id='8ab4155e-6b15-b885-9ce5-bed69a2c305c', name='call_model', path=('**pregel_pull', 'call_model'), error=None, interrupts=(), state=None, result={'messages': AIMessage(content='Your name is Bob.')}),),
interrupts=()
),
StateSnapshot(
values={'messages': [HumanMessage(content="hi! I'm bob"), AIMessage(content='Hi Bob! How are you doing today? Is there anything I can help you with?')]},
next=('**start**',),
config={...},
metadata={'source': 'input', 'writes': {'**start**': {'messages': [{'role': 'user', 'content': "what's my name?"}]}}, 'step': 2, 'parents': {}, 'thread_id': '1'},
created_at='2025-05-05T16:01:23.863173+00:00',
parent_config={...}
tasks=(PregelTask(id='24ba39d6-6db1-4c9b-f4c5-682aeaf38dcd', name='**start**', path=('**pregel_pull', '**start**'), error=None, interrupts=(), state=None, result={'messages': [{'role': 'user', 'content': "what's my name?"}]}),),
interrupts=()
),
StateSnapshot(
values={'messages': [HumanMessage(content="hi! I'm bob"), AIMessage(content='Hi Bob! How are you doing today? Is there anything I can help you with?')]},
next=(),
config={...},
metadata={'source': 'loop', 'writes': {'call_model': {'messages': AIMessage(content='Hi Bob! How are you doing today? Is there anything I can help you with?')}}, 'step': 1, 'parents': {}, 'thread_id': '1'},
created_at='2025-05-05T16:01:23.862295+00:00',
parent_config={...}
tasks=(),
interrupts=()
),
StateSnapshot(
values={'messages': [HumanMessage(content="hi! I'm bob")]},
next=('call_model',),
config={...},
metadata={'source': 'loop', 'writes': None, 'step': 0, 'parents': {}, 'thread_id': '1'},
created_at='2025-05-05T16:01:22.278960+00:00',
parent_config={...}
tasks=(PregelTask(id='8cbd75e0-3720-b056-04f7-71ac805140a0', name='call_model', path=('**pregel_pull', 'call_model'), error=None, interrupts=(), state=None, result={'messages': AIMessage(content='Hi Bob! How are you doing today? Is there anything I can help you with?')}),),
interrupts=()
),
StateSnapshot(
values={'messages': []},
next=('**start**',),
config={'configurable': {'thread_id': '1', 'checkpoint_ns': '', 'checkpoint_id': '1f029ca3-0870-6ce2-bfff-1f3f14c3e565'}},
metadata={'source': 'input', 'writes': {'**start**': {'messages': [{'role': 'user', 'content': "hi! I'm bob"}]}}, 'step': -1, 'parents': {}, 'thread_id': '1'},
created_at='2025-05-05T16:01:22.277497+00:00',
parent_config=None,
tasks=(PregelTask(id='d458367b-8265-812c-18e2-33001d199ce6', name='**start**', path=('**pregel_pull', '**start**'), error=None, interrupts=(), state=None, result={'messages': [{'role': 'user', 'content': "hi! I'm bob"}]}),),
interrupts=()
)
]

Delete all checkpoints for a thread¶

thread_id = "1"
checkpointer.delete_thread(thread_id)
