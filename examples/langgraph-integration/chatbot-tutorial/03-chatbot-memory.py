"""

FEATURES:
      [X] Tools
      [X] Memory

"""

import asyncio
from email import message
import os
from typing import Annotated
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from flowgentic import ChatOpenRouter
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel
from langgraph.checkpoint.memory import InMemorySaver

from dotenv import load_dotenv

load_dotenv()


class WorkflowState(BaseModel):
	messages: Annotated[list, add_messages]


llm = ChatOpenRouter(model="google/gemini-2.5-flash")


@tool
async def weather_extractor(city: str):
	"""Extracts the weather for any given city"""
	return {"temperature_celsius": 25, "humidity_percentage": 40}  # Dummy example


tools = [weather_extractor]
llm_with_tools = llm.bind_tools(tools)


async def invoke_llm(state: WorkflowState):
	response = await llm_with_tools.ainvoke(state.messages)
	return WorkflowState(messages=[response])


async def needs_tool_invokation(state: WorkflowState) -> str:
	last_message = state.messages[-1]
	if (
		hasattr(last_message, "tool_calls") and last_message.tool_calls
	):  # Ensuring there is a tool call attr and is not empty
		return "true"
	return "false"


workflow = StateGraph(WorkflowState)
workflow.add_node("chatbot", invoke_llm)
workflow.set_entry_point("chatbot")
workflow.add_node("tools", ToolNode(tools))
workflow.add_conditional_edges(
	"chatbot", needs_tool_invokation, {"true": "tools", "false": END}
)
workflow.add_edge("tools", "chatbot")

# Memory mechanism
memory = InMemorySaver()
app = workflow.compile(checkpointer=memory)
conversation_id = "1"
config = {"configurable": {"thread_id": conversation_id}}


async def start_app():
	while True:
		user_input = input("User: ").lower()
		if user_input in ["quit", "q", "-q", "exit"]:
			print(f"Goodbye!")
			return

		initial_state = WorkflowState(messages=[HumanMessage(content=user_input)])

		async for chunk in app.astream(
			input=initial_state, config=config, stream_mode="values"
		):
			if chunk["messages"]:
				last_msg = chunk["messages"][-1]
				if isinstance(last_msg, AIMessage):
					if hasattr(last_msg, "content") and last_msg.content:
						print(f"Assistant: {last_msg.content}")
					if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
						print(f"Tool calls: {last_msg.tool_calls}")
			print(chunk)
			print("=" * 30)


asyncio.run(start_app())

"""
Outputs:

User: My name is Javier. What is yours?
{'messages': [HumanMessage(content='what is the weather in san francisco?', additional_kwargs={}, response_metadata={}, id='152e2d8e-3f8e-4360-9441-7c8c3d5b4be0'), AIMessage(content='', additional_kwargs={'tool_calls': [{'id': 'tool_0_weather_extractor_zM8pFn8llFyGnhOKSQ6S', 'function': {'arguments': '{"city":"San Francisco"}', 'name': 'weather_extractor'}, 'type': 'function', 'index': 0}], 'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 16, 'prompt_tokens': 49, 'total_tokens': 65, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1758303279-kGhUYQUxXym1XcEfqX1o', 'service_tier': None, 'finish_reason': 'tool_calls', 'logprobs': None}, id='run--22d98ac4-e1e0-402c-b617-a372e3450113-0', tool_calls=[{'name': 'weather_extractor', 'args': {'city': 'San Francisco'}, 'id': 'tool_0_weather_extractor_zM8pFn8llFyGnhOKSQ6S', 'type': 'tool_call'}], usage_metadata={'input_tokens': 49, 'output_tokens': 16, 'total_tokens': 65, 'input_token_details': {'cache_read': 0}, 'output_token_details': {'reasoning': 0}}), ToolMessage(content='{"temperature_celsius": 25, "humidity_percentage": 40}', name='weather_extractor', id='abb510f7-2ea3-477b-8e00-72d009d03acf', tool_call_id='tool_0_weather_extractor_zM8pFn8llFyGnhOKSQ6S'), AIMessage(content='The weather in San Francisco is 25 degrees Celsius and the humidity is 40%.', additional_kwargs={'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 19, 'prompt_tokens': 112, 'total_tokens': 131, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1758303279-qEOo2ySFbG4PIIdyoav9', 'service_tier': None, 'finish_reason': 'stop', 'logprobs': None}, id='run--6b3826ff-b25c-426f-9b5f-548d7d43f30a-0', usage_metadata={'input_tokens': 112, 'output_tokens': 19, 'total_tokens': 131, 'input_token_details': {'cache_read': 0}, 'output_token_details': {'reasoning': 0}}), HumanMessage(content='my name is javier. what is yours?', additional_kwargs={}, response_metadata={}, id='8c47d0d3-eb57-4457-9cfd-d296c6e888ca')]}
==============================
Assistant: My name is Gemini. Nice to meet you Javier!

{'messages': [HumanMessage(content='what is the weather in san francisco?', additional_kwargs={}, response_metadata={}, id='152e2d8e-3f8e-4360-9441-7c8c3d5b4be0'), AIMessage(content='', additional_kwargs={'tool_calls': [{'id': 'tool_0_weather_extractor_zM8pFn8llFyGnhOKSQ6S', 'function': {'arguments': '{"city":"San Francisco"}', 'name': 'weather_extractor'}, 'type': 'function', 'index': 0}], 'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 16, 'prompt_tokens': 49, 'total_tokens': 65, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1758303279-kGhUYQUxXym1XcEfqX1o', 'service_tier': None, 'finish_reason': 'tool_calls', 'logprobs': None}, id='run--22d98ac4-e1e0-402c-b617-a372e3450113-0', tool_calls=[{'name': 'weather_extractor', 'args': {'city': 'San Francisco'}, 'id': 'tool_0_weather_extractor_zM8pFn8llFyGnhOKSQ6S', 'type': 'tool_call'}], usage_metadata={'input_tokens': 49, 'output_tokens': 16, 'total_tokens': 65, 'input_token_details': {'cache_read': 0}, 'output_token_details': {'reasoning': 0}}), ToolMessage(content='{"temperature_celsius": 25, "humidity_percentage": 40}', name='weather_extractor', id='abb510f7-2ea3-477b-8e00-72d009d03acf', tool_call_id='tool_0_weather_extractor_zM8pFn8llFyGnhOKSQ6S'), AIMessage(content='The weather in San Francisco is 25 degrees Celsius and the humidity is 40%.', additional_kwargs={'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 19, 'prompt_tokens': 112, 'total_tokens': 131, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1758303279-qEOo2ySFbG4PIIdyoav9', 'service_tier': None, 'finish_reason': 'stop', 'logprobs': None}, id='run--6b3826ff-b25c-426f-9b5f-548d7d43f30a-0', usage_metadata={'input_tokens': 112, 'output_tokens': 19, 'total_tokens': 131, 'input_token_details': {'cache_read': 0}, 'output_token_details': {'reasoning': 0}}), HumanMessage(content='my name is javier. what is yours?', additional_kwargs={}, response_metadata={}, id='8c47d0d3-eb57-4457-9cfd-d296c6e888ca'), AIMessage(content='My name is Gemini. Nice to meet you Javier!\n', additional_kwargs={'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 11, 'prompt_tokens': 143, 'total_tokens': 154, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1758303294-8acP7Lyojvi27JTrfiTY', 'service_tier': None, 'finish_reason': 'stop', 'logprobs': None}, id='run--7412ad13-e6cb-42de-924d-8c35ec71b6aa-0', usage_metadata={'input_tokens': 143, 'output_tokens': 11, 'total_tokens': 154, 'input_token_details': {'cache_read': 0}, 'output_token_details': {'reasoning': 0}})]}
==============================
User: What is my name?
{'messages': [HumanMessage(content='what is the weather in san francisco?', additional_kwargs={}, response_metadata={}, id='152e2d8e-3f8e-4360-9441-7c8c3d5b4be0'), AIMessage(content='', additional_kwargs={'tool_calls': [{'id': 'tool_0_weather_extractor_zM8pFn8llFyGnhOKSQ6S', 'function': {'arguments': '{"city":"San Francisco"}', 'name': 'weather_extractor'}, 'type': 'function', 'index': 0}], 'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 16, 'prompt_tokens': 49, 'total_tokens': 65, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1758303279-kGhUYQUxXym1XcEfqX1o', 'service_tier': None, 'finish_reason': 'tool_calls', 'logprobs': None}, id='run--22d98ac4-e1e0-402c-b617-a372e3450113-0', tool_calls=[{'name': 'weather_extractor', 'args': {'city': 'San Francisco'}, 'id': 'tool_0_weather_extractor_zM8pFn8llFyGnhOKSQ6S', 'type': 'tool_call'}], usage_metadata={'input_tokens': 49, 'output_tokens': 16, 'total_tokens': 65, 'input_token_details': {'cache_read': 0}, 'output_token_details': {'reasoning': 0}}), ToolMessage(content='{"temperature_celsius": 25, "humidity_percentage": 40}', name='weather_extractor', id='abb510f7-2ea3-477b-8e00-72d009d03acf', tool_call_id='tool_0_weather_extractor_zM8pFn8llFyGnhOKSQ6S'), AIMessage(content='The weather in San Francisco is 25 degrees Celsius and the humidity is 40%.', additional_kwargs={'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 19, 'prompt_tokens': 112, 'total_tokens': 131, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1758303279-qEOo2ySFbG4PIIdyoav9', 'service_tier': None, 'finish_reason': 'stop', 'logprobs': None}, id='run--6b3826ff-b25c-426f-9b5f-548d7d43f30a-0', usage_metadata={'input_tokens': 112, 'output_tokens': 19, 'total_tokens': 131, 'input_token_details': {'cache_read': 0}, 'output_token_details': {'reasoning': 0}}), HumanMessage(content='my name is javier. what is yours?', additional_kwargs={}, response_metadata={}, id='8c47d0d3-eb57-4457-9cfd-d296c6e888ca'), AIMessage(content='My name is Gemini. Nice to meet you Javier!\n', additional_kwargs={'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 11, 'prompt_tokens': 143, 'total_tokens': 154, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1758303294-8acP7Lyojvi27JTrfiTY', 'service_tier': None, 'finish_reason': 'stop', 'logprobs': None}, id='run--7412ad13-e6cb-42de-924d-8c35ec71b6aa-0', usage_metadata={'input_tokens': 143, 'output_tokens': 11, 'total_tokens': 154, 'input_token_details': {'cache_read': 0}, 'output_token_details': {'reasoning': 0}}), HumanMessage(content='what is my name?', additional_kwargs={}, response_metadata={}, id='692fb2bd-17b1-4bfc-b0bf-97a924cbc9f7')]}
==============================
Assistant: Your name is Javier.
{'messages': [HumanMessage(content='what is the weather in san francisco?', additional_kwargs={}, response_metadata={}, id='152e2d8e-3f8e-4360-9441-7c8c3d5b4be0'), AIMessage(content='', additional_kwargs={'tool_calls': [{'id': 'tool_0_weather_extractor_zM8pFn8llFyGnhOKSQ6S', 'function': {'arguments': '{"city":"San Francisco"}', 'name': 'weather_extractor'}, 'type': 'function', 'index': 0}], 'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 16, 'prompt_tokens': 49, 'total_tokens': 65, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1758303279-kGhUYQUxXym1XcEfqX1o', 'service_tier': None, 'finish_reason': 'tool_calls', 'logprobs': None}, id='run--22d98ac4-e1e0-402c-b617-a372e3450113-0', tool_calls=[{'name': 'weather_extractor', 'args': {'city': 'San Francisco'}, 'id': 'tool_0_weather_extractor_zM8pFn8llFyGnhOKSQ6S', 'type': 'tool_call'}], usage_metadata={'input_tokens': 49, 'output_tokens': 16, 'total_tokens': 65, 'input_token_details': {'cache_read': 0}, 'output_token_details': {'reasoning': 0}}), ToolMessage(content='{"temperature_celsius": 25, "humidity_percentage": 40}', name='weather_extractor', id='abb510f7-2ea3-477b-8e00-72d009d03acf', tool_call_id='tool_0_weather_extractor_zM8pFn8llFyGnhOKSQ6S'), AIMessage(content='The weather in San Francisco is 25 degrees Celsius and the humidity is 40%.', additional_kwargs={'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 19, 'prompt_tokens': 112, 'total_tokens': 131, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1758303279-qEOo2ySFbG4PIIdyoav9', 'service_tier': None, 'finish_reason': 'stop', 'logprobs': None}, id='run--6b3826ff-b25c-426f-9b5f-548d7d43f30a-0', usage_metadata={'input_tokens': 112, 'output_tokens': 19, 'total_tokens': 131, 'input_token_details': {'cache_read': 0}, 'output_token_details': {'reasoning': 0}}), HumanMessage(content='my name is javier. what is yours?', additional_kwargs={}, response_metadata={}, id='8c47d0d3-eb57-4457-9cfd-d296c6e888ca'), AIMessage(content='My name is Gemini. Nice to meet you Javier!\n', additional_kwargs={'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 11, 'prompt_tokens': 143, 'total_tokens': 154, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1758303294-8acP7Lyojvi27JTrfiTY', 'service_tier': None, 'finish_reason': 'stop', 'logprobs': None}, id='run--7412ad13-e6cb-42de-924d-8c35ec71b6aa-0', usage_metadata={'input_tokens': 143, 'output_tokens': 11, 'total_tokens': 154, 'input_token_details': {'cache_read': 0}, 'output_token_details': {'reasoning': 0}}), HumanMessage(content='what is my name?', additional_kwargs={}, response_metadata={}, id='692fb2bd-17b1-4bfc-b0bf-97a924cbc9f7'), AIMessage(content='Your name is Javier.', additional_kwargs={'refusal': None}, response_metadata={'token_usage': {'completion_tokens': 5, 'prompt_tokens': 161, 'total_tokens': 166, 'completion_tokens_details': {'accepted_prediction_tokens': None, 'audio_tokens': None, 'reasoning_tokens': 0, 'rejected_prediction_tokens': None, 'image_tokens': 0}, 'prompt_tokens_details': {'audio_tokens': None, 'cached_tokens': 0}}, 'model_name': 'google/gemini-2.5-flash', 'system_fingerprint': None, 'id': 'gen-1758303299-ITV68yyZBUUURW2tMJb8', 'service_tier': None, 'finish_reason': 'stop', 'logprobs': None}, id='run--cc6b1670-da36-4d27-8087-a631176e9dd4-0', usage_metadata={'input_tokens': 161, 'output_tokens': 5, 'total_tokens': 166, 'input_token_details': {'cache_read': 0}, 'output_token_details': {'reasoning': 0}})]}

"""
