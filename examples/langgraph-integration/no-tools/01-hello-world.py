import os

from typing import TypedDict, Annotated
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from dotenv import load_dotenv
load_dotenv()


# Define the state structure
class State(TypedDict):
    messages: Annotated[list, add_messages]

# Initialize the LLM with OpenRouter
llm = ChatOpenAI(
    model="google/gemini-2.5-flash",  # You can use other models available on OpenRouter
    openai_api_key=os.getenv("OPEN_ROUTER_API_KEY"),
    openai_api_base="https://openrouter.ai/api/v1",
)

# Define the chatbot node
def chatbot(state: State):
    """Simple chatbot node that responds to messages"""
    response = llm.invoke(state["messages"])
    return {"messages": [response]}

# Create the graph
graph = StateGraph(State)

# Add nodes
graph.add_node("chatbot", chatbot)

# Set entry point
graph.set_entry_point("chatbot")

# Add edge to end after chatbot responds
graph.add_edge("chatbot", END)

# Compile the graph
app = graph.compile()

# Test the hello world functionality
if __name__ == "__main__":
    print("LangGraph + OpenRouter Hello World!")
    print("-" * 40)
    
    # Create initial state with a hello world message
    initial_state = {
        "messages": [HumanMessage(content="Hello World! Please introduce yourself.")]
    }
    
    # Run the graph
    result = app.invoke(initial_state)
    
    # Print the conversation
    for message in result["messages"]:
        if hasattr(message, 'content'):
            role = "Human" if message.__class__.__name__ == "HumanMessage" else "Assistant"
            print(f"{role}: {message.content}")
            print()