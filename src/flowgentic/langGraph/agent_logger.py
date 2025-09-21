import os
from typing import List, Union, Dict, Any
from datetime import datetime
from uuid import uuid4
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage


class AgentLogger:
	@staticmethod
	def flush_agent_conversation(
		conversation_history: List[
			Union[HumanMessage, AIMessage, SystemMessage, ToolMessage]
		],
	):
		"""
		Flush agent conversation to markdown file with comprehensive analysis
		"""
		# Ensure directory exists
		os.makedirs("./agent_run_data", exist_ok=True)

		# Generate timestamp for this session
		timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

		# Analyze conversation
		analysis = AgentLogger._analyze_conversation(conversation_history)

		# Generate markdown content
		markdown_content = AgentLogger._generate_markdown_report(analysis, timestamp)

		# Write to file (append mode to keep historical data)
		with open(
			f"./agent_run_data/agent_interactions_{uuid4()}.md", "w", encoding="utf-8"
		) as f:
			f.write(markdown_content)

		print(
			f"✅ Agent conversation logged to ./agent_run_data/agent_interactions_{uuid4()}.md"
		)
		print(
			f"📊 Session summary: {analysis['total_messages']} messages, {analysis['total_tokens']} tokens"
		)

	@staticmethod
	def _analyze_conversation(
		conversation_history: List[
			Union[HumanMessage, AIMessage, SystemMessage, ToolMessage]
		],
	) -> Dict[str, Any]:
		"""Analyze conversation history and extract key metrics"""

		analysis = {
			"total_messages": len(conversation_history),
			"message_breakdown": {"human": 0, "ai": 0, "system": 0, "tool": 0},
			"total_tokens": 0,
			"token_breakdown": {"input": 0, "output": 0, "total": 0},
			"tool_calls": [],
			"models_used": set(),
			"conversation_flow": [],
			"execution_times": [],
			"first_message_time": None,
			"last_message_time": None,
		}

		for i, msg in enumerate(conversation_history):
			msg_analysis = {
				"index": i + 1,
				"type": msg.__class__.__name__.replace("Message", "").lower(),
				"content_preview": AgentLogger._get_content_preview(msg),
				"length": len(str(msg.content)) if hasattr(msg, "content") else 0,
				"tokens": 0,
			}

			# Count message types
			msg_type = msg_analysis["type"]
			if msg_type in analysis["message_breakdown"]:
				analysis["message_breakdown"][msg_type] += 1

			# Extract token usage for AI messages
			if (
				isinstance(msg, AIMessage)
				and hasattr(msg, "usage_metadata")
				and msg.usage_metadata
			):
				tokens = msg.usage_metadata
				msg_analysis["tokens"] = tokens.get("total_tokens", 0)
				analysis["total_tokens"] += msg_analysis["tokens"]
				analysis["token_breakdown"]["input"] += tokens.get("input_tokens", 0)
				analysis["token_breakdown"]["output"] += tokens.get("output_tokens", 0)
				analysis["token_breakdown"]["total"] += tokens.get("total_tokens", 0)

			# Extract model information
			if isinstance(msg, AIMessage) and hasattr(msg, "response_metadata"):
				model_name = msg.response_metadata.get("model_name", "unknown")
				analysis["models_used"].add(model_name)

			# Extract tool calls
			if (
				isinstance(msg, AIMessage)
				and hasattr(msg, "tool_calls")
				and msg.tool_calls
			):
				for tool_call in msg.tool_calls:
					analysis["tool_calls"].append(
						{
							"message_index": i + 1,
							"tool_name": tool_call.get("name", "unknown"),
							"tool_args": tool_call.get("args", {}),
							"tool_id": tool_call.get("id", "unknown"),
						}
					)
					msg_analysis["tool_calls"] = len(msg.tool_calls)

			# Extract timing information if available
			if hasattr(msg, "additional_kwargs") or hasattr(msg, "response_metadata"):
				# Try to extract timing from various possible locations
				pass  # Timing extraction would depend on your specific setup

			analysis["conversation_flow"].append(msg_analysis)

		# Convert sets to lists for JSON serialization
		analysis["models_used"] = list(analysis["models_used"])

		return analysis

	@staticmethod
	def _get_content_preview(msg, max_length=100) -> str:
		"""Get a preview of message content"""
		if isinstance(msg, ToolMessage):
			return f"Tool Response: {str(msg.content)[:max_length]}..."
		elif hasattr(msg, "content"):
			content = str(msg.content)
			if len(content) > max_length:
				return content[:max_length] + "..."
			return content
		else:
			return "No content"

	@staticmethod
	def _generate_markdown_report(analysis: Dict[str, Any], timestamp: str) -> str:
		"""Generate markdown report from analysis"""

		markdown = f"""
---

## Agent Conversation Session
**Timestamp:** {timestamp}  
**Total Duration:** Session completed  
**Thread ID:** Available in state metadata  

---

### 📊 Session Summary

| Metric | Value |
|--------|-------|
| **Total Messages** | {analysis["total_messages"]} |
| **Total Tokens** | {analysis["total_tokens"]:,} |
| **Tool Calls Made** | {len(analysis["tool_calls"])} |
| **Models Used** | {", ".join(analysis["models_used"]) if analysis["models_used"] else "None"} |

### 📈 Message Breakdown

| Message Type | Count | Percentage |
|-------------|-------|------------|
| **Human** | {analysis["message_breakdown"]["human"]} | {(analysis["message_breakdown"]["human"] / analysis["total_messages"] * 100):.1f}% |
| **AI** | {analysis["message_breakdown"]["ai"]} | {(analysis["message_breakdown"]["ai"] / analysis["total_messages"] * 100):.1f}% |
| **Tool** | {analysis["message_breakdown"]["tool"]} | {(analysis["message_breakdown"]["tool"] / analysis["total_messages"] * 100):.1f}% |
| **System** | {analysis["message_breakdown"]["system"]} | {(analysis["message_breakdown"]["system"] / analysis["total_messages"] * 100):.1f}% |

### 🔄 Token Usage Analysis

| Token Type | Count |
|------------|-------|
| **Input Tokens** | {analysis["token_breakdown"]["input"]:,} |
| **Output Tokens** | {analysis["token_breakdown"]["output"]:,} |
| **Total Tokens** | {analysis["token_breakdown"]["total"]:,} |

"""

		# Add tool calls section if any
		if analysis["tool_calls"]:
			markdown += "### 🛠 Tool Calls Summary\n\n"
			for i, tool_call in enumerate(analysis["tool_calls"], 1):
				markdown += f"**{i}.** `{tool_call['tool_name']}` (Message #{tool_call['message_index']})\n"
				if tool_call["tool_args"]:
					markdown += f"   - Args: `{tool_call['tool_args']}`\n"
				markdown += "\n"

		# Add conversation flow
		markdown += "### 💬 Conversation Flow\n\n"

		for msg in analysis["conversation_flow"]:
			icon = AgentLogger._get_message_icon(msg["type"])
			markdown += f"**{msg['index']}.** {icon} **{msg['type'].title()}**"

			if msg["tokens"] > 0:
				markdown += f" ({msg['tokens']} tokens)"

			if msg.get("tool_calls", 0) > 0:
				markdown += f" [{msg['tool_calls']} tool calls]"

			markdown += "\n"
			markdown += f"   ```\n   {msg['content_preview']}\n   ```\n\n"

		markdown += "---\n\n"

		return markdown

	@staticmethod
	def _get_message_icon(msg_type: str) -> str:
		"""Get emoji icon for message type"""
		icons = {"human": "👤", "ai": "🤖", "tool": "🔧", "system": "⚙️"}
		return icons.get(msg_type, "📝")
