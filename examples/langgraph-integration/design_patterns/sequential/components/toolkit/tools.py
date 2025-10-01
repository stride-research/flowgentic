from flowgentic.langGraph.agents import AsyncFlowType
import asyncio
from typing import Dict, Any

from ...utils.schemas import ValidationData, AgentOutput, ContextData, WorkflowState

from flowgentic.langGraph.base_components import BaseAgentTools, BaseUtilsTasks


class ResearchTools(BaseAgentTools):
	"""Research-specific agent tools."""

	def __init__(self, agents_manager):
		super().__init__(agents_manager)

	def register_tools(self):
		"""Register research agent tools."""

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.TOOL)
		async def web_search_tool(query: str) -> str:
			"""Search the web for information."""
			await asyncio.sleep(1)  # Simulate network delay
			return f"Search results for '{query}': Found relevant information about renewable energy storage, including battery technologies, grid integration, and market trends."

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.TOOL)
		async def data_analysis_tool(data: str) -> Dict[str, Any]:
			"""Analyze data and return insights."""
			await asyncio.sleep(0.5)
			return {
				"insights": f"Analysis reveals key trends in '{data[:50]}...'",
				"confidence": 0.85,
				"key_points": [
					"Lithium-ion costs dropping 15% annually",
					"Grid-scale storage growing 40% YoY",
					"Solid-state batteries emerging as next breakthrough",
				],
				"market_size": "$12.1B by 2025",
			}

		self.tools = {
			"web_search": web_search_tool,
			"data_analysis": data_analysis_tool,
		}
		return self.tools


class SynthesisTools(BaseAgentTools):
	"""Synthesis-specific agent tools."""

	def __init__(self, agents_manager):
		super().__init__(agents_manager)

	def register_tools(self):
		"""Register synthesis agent tools."""

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.TOOL)
		async def document_generator_tool(content: Dict[str, Any]) -> str:
			"""Generate a formatted document from analysis results."""
			await asyncio.sleep(0.3)
			key_points = content.get("key_points", [])
			return f"Executive Summary: Generated comprehensive report covering {len(key_points)} critical insights with {content.get('confidence', 0) * 100:.0f}% confidence level."

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.TOOL)
		async def report_formatter_tool(content: str) -> str:
			"""Format content into a professional report structure."""
			await asyncio.sleep(0.2)
			return f"[FORMATTED REPORT]\n\n{content}\n\n[END REPORT]"

		self.tools = {
			"document_generator": document_generator_tool,
			"report_formatter": report_formatter_tool,
		}
		return self.tools


class ValidationTasks(BaseUtilsTasks):
	"""Input validation and preprocessing tasks."""

	def __init__(self, agents_manager):
		super().__init__(agents_manager)

	def register_tasks(self):
		"""Register validation tasks."""

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.UTISL_TASK)
		async def validate_input_task(user_input: str) -> ValidationData:
			"""Validate and preprocess user input - deterministic operation."""
			validation_result = ValidationData(
				is_valid=len(user_input.strip()) > 0,
				cleaned_input=user_input.strip(),
				word_count=len(user_input.split()),
				timestamp=asyncio.get_event_loop().time(),
				metadata={
					"has_keywords": any(
						word in user_input.lower()
						for word in ["research", "analyze", "report"]
					),
					"complexity_score": min(len(user_input.split()) / 10, 1.0),
					"domain": "energy" if "energy" in user_input.lower() else "general",
				},
			)
			return validation_result

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.UTISL_TASK)
		async def security_scan_task(user_input: str) -> Dict[str, Any]:
			"""Perform security scanning on user input."""
			await asyncio.sleep(0.1)
			return {
				"is_safe": True,
				"risk_score": 0.1,
				"detected_patterns": [],
				"sanitized_input": user_input.strip(),
			}

		self.tasks = {
			"validate_input": validate_input_task,
			"security_scan": security_scan_task,
		}
		return self.tasks


class ContextTasks(BaseUtilsTasks):
	"""Context preparation and management tasks."""

	def __init__(self, agents_manager):
		super().__init__(agents_manager)

	def register_tasks(self):
		"""Register context management tasks."""

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.UTISL_TASK)
		async def prepare_context_task(
			research_output: AgentOutput, validation_data: ValidationData
		) -> ContextData:
			"""Prepare context for the second agent - deterministic operation."""
			context = ContextData(
				previous_analysis=research_output.output_content,
				input_metadata=validation_data,
				processing_stage="context_preparation",
				agent_sequence=1,
				additional_context={
					"research_tools_used": research_output.tools_used,
					"research_execution_time": research_output.execution_time,
					"input_complexity": validation_data.metadata.get(
						"complexity_score", 0
					),
				},
			)
			return context

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.UTISL_TASK)
		async def enrich_context_task(
			context: ContextData, additional_data: Dict[str, Any]
		) -> ContextData:
			"""Enrich context with additional metadata."""
			await asyncio.sleep(0.1)
			context.additional_context.update(additional_data)
			return context

		self.tasks = {
			"prepare_context": prepare_context_task,
			"enrich_context": enrich_context_task,
		}
		return self.tasks


class FormattingTasks(BaseUtilsTasks):
	"""Output formatting and finalization tasks."""

	def __init__(self, agents_manager):
		super().__init__(agents_manager)

	def register_tasks(self):
		"""Register formatting tasks."""

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.UTISL_TASK)
		async def format_final_output_task(
			synthesis_output: AgentOutput, context: ContextData
		) -> str:
			"""Format the final output - deterministic operation."""
			formatted_output = f"""
=== SEQUENTIAL REACT AGENT WORKFLOW RESULTS ===
Input processed at: {context.input_metadata.timestamp}
Word count: {context.input_metadata.word_count}
Domain: {context.input_metadata.metadata.get("domain", "unknown")}

=== RESEARCH AGENT OUTPUT ===
Agent: {context.additional_context.get("research_agent_name", "Research Agent")}
Tools Used: {", ".join(context.additional_context.get("research_tools_used", []))}
Execution Time: {context.additional_context.get("research_execution_time", 0):.2f}s

{context.previous_analysis}

=== SYNTHESIS AGENT OUTPUT ===
Agent: {synthesis_output.agent_name}
Tools Used: {", ".join(synthesis_output.tools_used)}
Execution Time: {synthesis_output.execution_time:.2f}s

{synthesis_output.output_content}

=== WORKFLOW COMPLETE ===
Total Processing Time: {synthesis_output.execution_time + context.additional_context.get("research_execution_time", 0):.2f}s
"""
			return formatted_output.strip()

		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.UTISL_TASK)
		async def generate_summary_task(workflow_state: WorkflowState) -> str:
			"""Generate a workflow execution summary."""
			await asyncio.sleep(0.1)
			return f"Workflow completed in {workflow_state.current_stage} with {len(workflow_state.errors)} errors."

		self.tasks = {
			"format_final_output": format_final_output_task,
			"generate_summary": generate_summary_task,
		}
		return self.tasks
