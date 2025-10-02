import pickle
from langgraph.graph import add_messages
import pytest
from typing import Annotated, List
from pydantic import BaseModel, Field
from datetime import datetime

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from flowgentic.langGraph.telemetry.introspection import GraphIntrospector

import logging

logger = logging.getLogger(__name__)


class TestState(BaseModel):
	messages: Annotated[List[BaseMessage], add_messages] = []
	counter: int = 1


@pytest.mark.asyncio
async def test_state_extraction_via_introspect_node():
	introspector = GraphIntrospector()

	async def sample_node(state: TestState) -> TestState:
		"""A great description for a great sample node"""
		file_handler = open("tests/utils/response.obj", "rb")
		msg = pickle.load(file_handler)
		state.messages.extend(msg.get("messages"))
		state.counter += 1
		return state

	wrapped = introspector.introspect_node(sample_node, node_name="sample_node")

	before = TestState()
	after = await wrapped(before)
	logger.debug(f"RECORD IS: {introspector._records}")

	# Return type
	assert isinstance(after, TestState)
	assert len(after.messages) == 5

	# Records
	assert len(introspector._records) == 1
	record = list(introspector._records.values())[-1]

	# Node metadata
	assert record.node_name == "sample_node"
	assert record.description == "A great description for a great sample node"
	# Message added
	assert record.total_messages_before == 0
	assert record.total_messages_after == 5
	assert record.new_messages_count == 5
	assert len(record.messages_added) == 5

	# Reasoning
	assert (
		record.final_response
		== "Based on the provided research, I have generated a comprehensive synthesis report with actionable recommendations tailored for your clean energy startup. Here is the document:\n Executive Summary: Succesfully generated comprehensive report covering 0 critical insights"
	)

	# Token usage
	assert record.token_usage is not None
	assert record.token_usage.input_tokens == 1239
	assert record.token_usage.output_tokens == 2532
	assert record.token_usage.total_tokens == 3771
	assert record.token_usage.cache_read_tokens == 0
	assert record.token_usage.reasoning_tokens == 1856

	# Metadata
	assert record.model_metadata is not None
	assert record.model_metadata.model_name == "google/gemini-2.5-pro"

	# Tool calls
	assert len(record.tool_calls) == 1
	tc = record.tool_calls[0]
	assert tc.tool_name == "document_generator_tool"
	assert len(tc.tool_args) == 1
	assert "content" in tc.tool_args
	assert tc.tool_call_id == "tool_0_document_generator_tool_Wk0PG3n4kFNJ0GcbUpLV"

	# Tool invokation
	assert len(record.tool_executions) == 1
	texec = record.tool_executions[0]
	assert texec.tool_name == "document_generator_tool"
	assert len(texec.tool_response) == 90
	assert (
		texec.tool_response
		== "Executive Summary: Succesfully generated comprehensive report covering 0 critical insights"
	)
	assert texec.tool_call_id == "tool_0_document_generator_tool_Wk0PG3n4kFNJ0GcbUpLV"
	assert texec.tool_status == "success"

	# Final state
	assert set(record.state_keys) == {"messages", "counter"}
	assert "counter" in record.state_diff
	assert record.state_diff["counter"]["changed_from"] == "1"
	assert record.state_diff["counter"]["changed_to"] == "2"
	assert "messages" in record.state_diff
	assert record.start_time <= record.end_time
	assert record.duration_seconds >= 0
