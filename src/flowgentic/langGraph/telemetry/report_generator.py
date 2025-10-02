from datetime import datetime
import sys
from typing import Dict, List

from pydantic import BaseModel

from flowgentic.langGraph.main import LangraphIntegration
from .schemas import GraphExecutionReport, NodeExecutionRecord
from flowgentic.settings.extract_settings import APP_SETTINGS
import logging

logger = logging.getLogger(__name__)


class ReportGenerator:
	def __init__(
		self,
		final_state: BaseModel,
		records: Dict[str, NodeExecutionRecord],
		start_time,
	) -> None:
		self._start_time = start_time
		self._final_state = final_state
		self._records = records
		self._records_values = list(self._records.values())

	def generate_report(self, all_nodes: List[str]):
		"""Generates a human-readable Markdown report of the entire graph execution."""
		end_time = datetime.now()

		# Calculate aggregate statistics
		total_tokens = sum(
			(r.token_usage.total_tokens if r.token_usage else 0)
			for r in self._records_values
		)
		total_tool_calls = sum(len(r.tool_calls) for r in self._records_values)
		total_tool_executions = sum(
			len(r.tool_executions) for r in self._records_values
		)
		total_messages = (
			self._records_values[-1].total_messages_after if self._records_values else 0
		)
		models_used = list(
			set(
				r.model_metadata.model_name
				for r in self._records_values
				if r.model_metadata and r.model_metadata.model_name
			)
		)

		report_data = GraphExecutionReport(
			graph_start_time=self._start_time,
			graph_end_time=end_time,
			total_duration_seconds=round(
				(end_time - self._start_time).total_seconds(), 4
			),
			node_records=self._records_values,
			final_state=self._final_state.model_dump(),
			total_tokens_used=total_tokens,
			total_tool_calls=total_tool_calls,
			total_tool_executions=total_tool_executions,
			total_messages=total_messages,
			models_used=models_used,
		)
		output_path = APP_SETTINGS["agent_execution"]["execution_summary_path"]
		with open(output_path, "w", encoding="utf-8") as f:
			f.write(f"# 📊 LangGraph Execution Report\n\n")
			f.write(
				f"**Generated on:** `{report_data.graph_start_time.strftime('%Y-%m-%d %H:%M:%S')}`\n"
			)
			f.write(
				f"**Total Duration:** `{report_data.total_duration_seconds} seconds`\n\n"
			)

			# Aggregate Statistics
			f.write("## 📈 Aggregate Statistics\n\n")
			f.write(f"- **Total Tokens Used:** `{report_data.total_tokens_used:,}`\n")
			f.write(f"- **Total Tool Calls:** `{report_data.total_tool_calls}`\n")
			f.write(
				f"- **Total Tool Executions:** `{report_data.total_tool_executions}`\n"
			)
			f.write(f"- **Total Messages:** `{report_data.total_messages}`\n")
			f.write(
				f"- **Models Used:** `{', '.join(report_data.models_used) if report_data.models_used else 'None'}`\n"
			)
			f.write(f"- **Number of Nodes:** `{len(report_data.node_records)}`\n\n")

			f.write(f"--- \n\n")

			f.write("## 📝 Execution Summary\n\n")
			f.write(
				"| Node Name           | Duration (s) | Tokens | Tools | New Messages |\n"
			)
			f.write(
				"|---------------------|--------------|--------|-------|---------------|\n"
			)
			for node_name in all_nodes:
				logger.debug(
					f"Node name is: {node_name}, list to search in is: {list(self._records.keys())}"
				)
				if node_name in list(self._records.keys()):
					record = self._records[node_name]
					tools_count = len(record.tool_calls)
					tokens = (
						record.token_usage.total_tokens if record.token_usage else 0
					)
					f.write(
						f"| `{record.node_name}` | {record.duration_seconds:<12.4f} | {tokens:<6} | {tools_count:<5} | {record.new_messages_count:<13} |\n"
					)
				else:
					f.write(
						f"| `{node_name}` | not visited | {'<n/a>':<6} | {'<n/a>':<5} | {'<n/a>':<13} |\n"
					)

			f.write("\n\n")

			f.write("## 🔍 Node Details\n\n")
			for i, record in enumerate(report_data.node_records):
				f.write(f"--- \n\n")
				f.write(f"### {i + 1}. Node: `{record.node_name}`\n\n")
				if record.description:
					f.write(f"**Description:**\n```\n{record.description}\n```\n\n")
				f.write(
					f"- **Timestamp:** `{record.start_time.strftime('%H:%M:%S.%f')[:-3]}`\n"
				)
				f.write(f"- **Duration:** `{record.duration_seconds} seconds`\n")
				f.write(
					f"- **Messages Before/After:** `{record.total_messages_before}` → `{record.total_messages_after}` (➕ {record.new_messages_count})\n"
				)
				f.write(f"- **State Keys:** `{', '.join(record.state_keys)}`\n")

				if record.model_metadata:
					f.write(f"\n**🤖 Model Information:**\n")
					f.write(f"- **Model Name:** `{record.model_metadata.model_name}`\n")
					if record.model_metadata.finish_reason:
						f.write(
							f"- **Finish Reason:** `{record.model_metadata.finish_reason}`\n"
						)
					if record.model_metadata.system_fingerprint:
						f.write(
							f"- **System Fingerprint:** `{record.model_metadata.system_fingerprint}`\n"
						)

				if record.token_usage:
					f.write(f"\n**📊 Token Usage:**\n")
					f.write(
						f"- **Input Tokens:** `{record.token_usage.input_tokens:,}`\n"
					)
					f.write(
						f"- **Output Tokens:** `{record.token_usage.output_tokens:,}`\n"
					)
					f.write(
						f"- **Total Tokens:** `{record.token_usage.total_tokens:,}`\n"
					)
					if record.token_usage.cache_read_tokens > 0:
						f.write(
							f"- **Cache Read Tokens:** `{record.token_usage.cache_read_tokens:,}`\n"
						)
					if record.token_usage.reasoning_tokens > 0:
						f.write(
							f"- **Reasoning Tokens:** `{record.token_usage.reasoning_tokens:,}`\n"
						)

				if record.final_response:
					f.write(
						f"\n**🤔 Model Final Response:**\n```text\n{record.final_response}\n```\n"
					)

				if record.tool_calls:
					f.write(f"\n**🛠️ Tool Calls ({len(record.tool_calls)}):**\n")
					for idx, tc in enumerate(record.tool_calls, 1):
						f.write(f"{idx}. **Tool:** `{tc.tool_name}`\n")
						if tc.tool_call_id:
							f.write(f"   - **Call ID:** `{tc.tool_call_id}`\n")
						f.write(f"   - **Arguments:** `{tc.tool_args}`\n")
				if record.tool_executions:
					f.write(
						f"\n**✅ Tool Executions ({len(record.tool_executions)}):**\n"
					)
					for idx, te in enumerate(record.tool_executions, 1):
						f.write(f"{idx}. **Tool:** `{te.tool_name}`\n")
						f.write(f"   - **Status:** `{te.tool_status}`\n")
						f.write(f"   - **Call ID:** `{te.tool_call_id}`\n")
						f.write(f"   - **Response:** `{te.tool_response}`\n")

				if record.interleaved_thinking:
					f.write(
						f"\n**🧠 Thinking Process ({len(record.interleaved_thinking)} steps):**\n"
					)
					for idx, thinking_step in enumerate(record.interleaved_thinking, 1):
						f.write(f"{idx}. =>  _{thinking_step}_\n")

				if record.state_diff:
					f.write(f"\n**🔄 State Changes:**\n")
					f.write("```json\n")
					import json

					f.write(json.dumps(record.state_diff, indent=2))
					f.write("\n```\n\n")

				if record.messages_added:
					f.write(
						f"\n **FULL CONVERSATION HISTORY FOR {record.node_name}:**\n"
					)
					f.write(
						f"\n**💬 Messages Added ({len(record.messages_added)}):**\n"
					)
					for idx, msg in enumerate(record.messages_added, 1):
						f.write(f"{idx}. **{msg.message_type}**")
						if msg.message_id:
							f.write(f" (ID: `{msg.message_id[:20]}...`)")
						f.write(f"\n")
						if msg.role:
							f.write(f"   - **Role:** `{msg.role}`\n")
						if msg.has_tool_calls:
							f.write(f"   - **Has Tool Calls:** ✅\n")
						if msg.tool_call_id:
							f.write(f"   - **Tool Call ID:** `{msg.tool_call_id}`\n")
						f.write(
							f"   - **Content:** `{msg.content[:200]}{'...' if len(msg.content) > 200 else ''}`\n"
						)

			f.write(f"--- \n\n")
			f.write("## ✅ Final State Summary\n\n")
			if self._final_state:
				# Get state keys - handle Pydantic model
				if hasattr(self._final_state, "model_fields"):
					state_keys = list(self._final_state.model_fields.keys())
					f.write(f"**State Keys:** `{', '.join(state_keys)}`\n\n")

					# Show summary of final state
					for key in state_keys:
						value = getattr(self._final_state, key, None)
						f.write(f"- **{key}:** {(value)}\n")

		print(f"✅ Introspection report saved to '{output_path}'")
