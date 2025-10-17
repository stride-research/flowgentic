"""
Memory-aware tools and tasks for sequential workflow.

This module defines agent tools and deterministic tasks that can access
and leverage memory context for improved decision-making and context-aware responses.
"""

import asyncio
from typing import Dict, Any, List
from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from ...utils.schemas import ValidationData


class ResearchTools:
	"""Memory-aware research tools."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager

	def register_tools(self) -> Dict[str, Any]:
		"""Register research tools with memory awareness."""

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def web_search(query: str) -> str:
			"""Search the web for information. Uses memory context to avoid redundant searches."""
			await asyncio.sleep(0.5)  # Simulate API call
			return f"🔍 Web search results for '{query}': Found comprehensive information on renewable energy storage, battery technologies, and market trends..."

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def data_analysis(data: str) -> Dict[str, Any]:
			"""Analyze data and extract insights. Memory-aware to build on previous analyses."""
			await asyncio.sleep(0.3)  # Simulate processing
			return {
				"insights": f"Analysis of '{data[:50]}...': Identified key trends in energy storage technology",
				"confidence": 0.87,
				"key_points": [
					"Battery technology advancing rapidly",
					"Grid integration challenges remain",
					"Market opportunity estimated at $50B by 2030",
				],
			}

		return {"web_search": web_search, "data_analysis": data_analysis}


class SynthesisTools:
	"""Memory-aware synthesis tools."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager

	def register_tools(self) -> Dict[str, Any]:
		"""Register synthesis tools with memory awareness."""

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def document_generator(content: str) -> str:
			"""Generate structured documents. Leverages memory for consistency."""
			await asyncio.sleep(0.4)
			return f"📄 Generated comprehensive document based on: {content[:100]}..."

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.AGENT_TOOL_AS_FUNCTION
		)
		async def recommendation_engine(analysis: str) -> Dict[str, Any]:
			"""Generate recommendations. Uses memory to ensure coherent advice."""
			await asyncio.sleep(0.3)
			return {
				"recommendations": [
					"Focus on lithium-ion battery optimization",
					"Invest in grid integration R&D",
					"Partner with utility companies for pilots",
				],
				"priority": "high",
				"confidence": 0.85,
			}

		return {
			"document_generator": document_generator,
			"recommendation_engine": recommendation_engine,
		}


class ValidationTasks:
	"""Memory-aware validation tasks."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager

	def register_function_tasks(self) -> Dict[str, Any]:
		"""Register validation tasks."""

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def validate_input(user_input: str) -> ValidationData:
			"""Validate and preprocess user input."""
			await asyncio.sleep(0.1)
			cleaned = user_input.strip()
			words = cleaned.split()

			# Determine domain from input
			domain = "general"
			if any(
				keyword in cleaned.lower()
				for keyword in ["energy", "battery", "renewable", "storage"]
			):
				domain = "energy"
			elif any(
				keyword in cleaned.lower()
				for keyword in ["finance", "investment", "market"]
			):
				domain = "finance"

			return ValidationData(
				is_valid=len(cleaned) > 0,
				cleaned_input=cleaned,
				word_count=len(words),
				timestamp=asyncio.get_event_loop().time(),
				metadata={"domain": domain, "complexity": "high" if len(words) > 20 else "medium"},
			)

		return {"validate_input": validate_input}


class ContextTasks:
	"""Memory-aware context preparation tasks."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager

	def register_function_tasks(self) -> Dict[str, Any]:
		"""Register context preparation tasks with memory integration."""

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def prepare_research_context(
			validation_data: ValidationData, memory_context: Dict[str, Any]
		) -> Dict[str, Any]:
			"""Prepare context for research agent using validation data and memory."""
			await asyncio.sleep(0.1)

			# Extract relevant memory insights
			memory_insights = memory_context.get("relevant_messages", [])
			memory_summary = f"Previous context: {len(memory_insights)} relevant messages"

			return {
				"domain": validation_data.metadata.get("domain", "general"),
				"complexity": validation_data.metadata.get("complexity", "medium"),
				"word_count": validation_data.word_count,
				"memory_context": memory_summary,
				"timestamp": validation_data.timestamp,
			}

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def prepare_synthesis_context(
			research_output: str, memory_context: Dict[str, Any]
		) -> Dict[str, Any]:
			"""Prepare context for synthesis agent using research output and memory."""
			await asyncio.sleep(0.1)

			# Leverage memory to identify key themes
			memory_stats = memory_context.get("memory_stats", {})

			return {
				"research_summary": research_output[:200] + "...",
				"memory_message_count": memory_stats.get("total_messages", 0),
				"memory_efficiency": memory_stats.get("memory_efficiency", 0.0),
				"key_themes": ["battery technology", "grid integration", "market analysis"],
			}

		return {
			"prepare_research_context": prepare_research_context,
			"prepare_synthesis_context": prepare_synthesis_context,
		}


class FormattingTasks:
	"""Memory-aware formatting tasks."""

	def __init__(self, agents_manager):
		self.agents_manager = agents_manager

	def register_function_tasks(self) -> Dict[str, Any]:
		"""Register formatting tasks with memory awareness."""

		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.FUNCTION_TASK
		)
		async def format_final_output(
			synthesis_output: str, memory_stats: Dict[str, Any]
		) -> str:
			"""Format final output including memory statistics."""
			await asyncio.sleep(0.1)

			formatted = f"""
=== MEMORY-ENABLED SEQUENTIAL WORKFLOW RESULTS ===

📊 Memory Statistics:
   - Total messages processed: {memory_stats.get('total_messages', 0)}
   - Memory efficiency: {memory_stats.get('memory_efficiency', 0.0):.1%}
   - Average importance: {memory_stats.get('average_importance', 0.0):.2f}
   - Interaction count: {memory_stats.get('interaction_count', 0)}

📝 Synthesis Output:
{synthesis_output}

✅ Workflow completed successfully with memory-aware processing
"""
			return formatted.strip()

		return {"format_final_output": format_final_output}
