import pickle
from langgraph.graph import add_messages
import pytest
from typing import Annotated, List
from pydantic import BaseModel, Field
from datetime import datetime, timedelta

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from flowgentic.utils.telemetry.introspection import GraphIntrospector
from flowgentic.utils.telemetry.report_generator import ReportGenerator
from flowgentic.utils.telemetry.schemas import (
	MessageInfo,
	ModelMetadata,
	NodeExecutionRecord,
	TokenUsage,
	ToolCallInfo,
	ToolExecutionInfo,
)

import logging

logger = logging.getLogger(__name__)


class FakeState(BaseModel):
	messages: Annotated[List[BaseMessage], add_messages]


async def test_cicylic_node_extraction():
	start_1 = datetime.now()
	end_1 = start_1 + timedelta(seconds=0.5)
	record_1 = NodeExecutionRecord(
		node_name="nodeA_1",
		description="Cleans and normalizes the input data.",
		start_time=start_1,
		end_time=end_1,
		duration_seconds=(end_1 - start_1).total_seconds(),
		total_messages_before=2,
		total_messages_after=3,
		new_messages_count=1,
		messages_added=[
			MessageInfo(
				content="Data successfully preprocessed.",
				role="system",
				message_type="typeA",
			)
		],
		state_diff={"data_status": "cleaned"},
		state_keys=["input_data", "data_status"],
	)

	# 2. LLM Call with Tool Usage
	start_2 = datetime.now() - timedelta(minutes=5)
	end_2 = start_2 + timedelta(seconds=15.3)
	record_2 = NodeExecutionRecord(
		node_name="nodeB_123123",
		description="Generates a plan and executes a search tool.",
		start_time=start_2,
		end_time=end_2,
		duration_seconds=15.3,
		total_messages_before=5,
		total_messages_after=8,
		new_messages_count=3,
		model_metadata=ModelMetadata(model_name="gemini-2.5-pro", temperature=0.7),
		final_response="The plan is set and the first step has been executed.",
		interleaved_thinking=[
			"Initial prompt received.",
			"Decided to use the search_tool for context.",
			"Parsed tool output and formulated final response.",
		],
		tool_calls=[
			ToolCallInfo(
				tool_name="search_engine", tool_args={"query": "latest AI news"}
			)
		],
		tool_executions=[
			ToolExecutionInfo(
				tool_name="search_engine",
				tool_status="Found 5 relevant articles.",
				tool_call_id="1233",
				tool_response="A response",
			)
		],
		token_usage=TokenUsage(input_tokens=150, output_tokens=75, total_tokens=225),
		state_diff={"plan_status": "executed_step_1", "search_result_count": 5},
		state_keys=["plan", "search_result_count", "conversation_history"],
	)

	# 3. Simple State Update Node (Quick and no LLM/Tool interaction)
	start_3 = datetime.now() - timedelta(minutes=10)
	end_3 = start_3 + timedelta(milliseconds=50)
	record_3 = NodeExecutionRecord(
		node_name="nodeA_123453",
		description="Updates the loop counter and checks for completion.",
		start_time=start_3,
		end_time=end_3,
		duration_seconds=0.05,
		total_messages_before=8,
		total_messages_after=8,  # No new messages added
		new_messages_count=0,
		state_diff={"loop_count": 3, "is_complete": False},
		state_keys=["loop_count", "max_iterations", "is_complete"],
	)

	records = {
		record_1.node_name: record_1,
		record_2.node_name: record_2,
		record_3.node_name: record_3,
	}

	current_time = datetime.now()
	state = FakeState(messages=[])
	state.messages = ["A final message"]
	report_generator = ReportGenerator(
		final_state=state, records=records, start_time=current_time
	)

	report_generator.generate_report(all_nodes=["nodeA", "nodeB"])
