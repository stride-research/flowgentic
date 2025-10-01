import sys
from typing import Dict

from flowgentic.langGraph.main import LangraphIntegration
from ..utils.schemas import WorkflowState, AgentOutput
from .toolkit.tool_registry import ToolsRegistry

import asyncio
from flowgentic.langGraph.agents import AsyncFlowType
from flowgentic.utils.llm_providers import ChatLLMProvider
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage
from flowgentic.langGraph.base_components import BaseWorkflowNodes

from dotenv import load_dotenv

load_dotenv()


class WorkflowNodes(BaseWorkflowNodes):
	"""Contains all workflow nodes with access to agents_manager and tools."""

	def __init__(
		self, agents_manager: LangraphIntegration, tools_registry: ToolsRegistry
	) -> None:
		super().__init__(agents_manager, tools_registry)

	def get_all_nodes(self) -> Dict[str, callable]:
		"""Return all node functions for graph registration."""
		return {
			"preprocess": self.preprocess_node,
			"research_agent": self.research_agent_node,
			"context_preparation": self.context_preparation_node,
			"synthesis_agent": self.synthesis_agent_node,
			"finalize_output": self.finalize_output_node,
			"error_handler": self.error_handler_node,
		}

	@property
	def preprocess_node(self):
		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.BLOCK)
		async def _preprocess_node(state: WorkflowState) -> WorkflowState:
			"""Preprocessing node with parallel validation and metadata extraction."""
			print("🔄 Preprocessing Node: Starting input validation...")

			try:
				validation_task = self.tools_registry.get_task_by_name(
					"validate_input"
				)(state.user_input)
				validation_data = await validation_task

				state.validation_data = validation_data
				state.preprocessing_complete = True
				state.current_stage = "preprocessing_complete"

				print(
					f"✅ Preprocessing complete: {validation_data.word_count} words, domain: {validation_data.metadata.get('domain')}"
				)

			except Exception as e:
				state.errors.append(f"Preprocessing error: {str(e)}")
				state.current_stage = "preprocessing_failed"

			return state

		return _preprocess_node

	@property
	def research_agent_node(self):
		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.BLOCK)
		async def _research_agent_node(state: WorkflowState) -> WorkflowState:
			"""Research agent execution node."""
			print("🔍 Research Agent Node: Starting research and analysis...")

			try:
				start_time = asyncio.get_event_loop().time()

				tools = [
					self.tools_registry.get_tool_by_name("web_search"),
					self.tools_registry.get_tool_by_name("data_analysis"),
				]

				research_agent = create_react_agent(
					model=ChatLLMProvider(
						provider="OpenRouter", model="google/gemini-2.5-flash"
					),
					tools=tools,
				)

				research_state = {
					"messages": [
						SystemMessage(
							content="You are a research agent specializing in technology analysis. Your job is to gather comprehensive information, analyze data, and provide detailed insights. Always use your tools to get the most current and accurate information."
						),
						HumanMessage(content=state.user_input),
					]
				}
				research_result = await research_agent.ainvoke(research_state)
				execution_time = asyncio.get_event_loop().time() - start_time

				if "messages" in research_result and isinstance(
					research_result["messages"], list
				):
					state.messages.extend(research_result["messages"])

				agent_output = AgentOutput(
					agent_name="Research Agent",
					output_content=research_result["messages"][-1].content,
					execution_time=execution_time,
					tools_used=["web_search_tool", "data_analysis_tool"],
					success=True,
				)

				state.research_agent_output = agent_output
				state.current_stage = "research_complete"

				print(f"✅ Research Agent complete in {execution_time:.2f}s")

				return state

			except Exception as e:
				error_msg = f"Research agent error: {str(e)}"
				state.errors.append(error_msg)
				state.research_agent_output = AgentOutput(
					agent_name="Research Agent",
					output_content="",
					execution_time=0,
					success=False,
					error_message=error_msg,
				)
				state.current_stage = "research_failed"

			return state

		return _research_agent_node

	@property
	def context_preparation_node(self):
		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.BLOCK)
		async def _context_preparation_node(state: WorkflowState) -> WorkflowState:
			"""Context preparation node - runs in parallel with other deterministic tasks."""
			print(
				"🔧 Context Preparation Node: Preparing context for synthesis agent..."
			)

			try:
				context_task = self.tools_registry.get_task_by_name("prepare_context")(
					state.research_agent_output, state.validation_data
				)
				context = await context_task

				state.context = context
				state.current_stage = "context_prepared"

				print("✅ Context preparation complete")

			except Exception as e:
				state.errors.append(f"Context preparation error: {str(e)}")
				state.current_stage = "context_preparation_failed"

			return state

		return _context_preparation_node

	@property
	def synthesis_agent_node(self):
		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.BLOCK)
		async def _synthesis_agent_node(state: WorkflowState) -> WorkflowState:
			"""Synthesis agent execution node."""
			print("🏗️ Synthesis Agent Node: Creating final deliverables...")

			try:
				start_time = asyncio.get_event_loop().time()

				tools = [self.tools_registry.get_tool_by_name("document_generator")]

				synthesis_agent = create_react_agent(
					model=ChatLLMProvider(
						provider="OpenRouter", model="google/gemini-2.5-flash"
					),
					tools=tools,
				)

				synthesis_input = f"""
Based on the research findings: {state.research_agent_output.output_content}

Context: {state.context.dict() if state.context else "No context available"}

Please create a comprehensive synthesis with clear recommendations for a clean energy startup focusing on renewable energy storage technologies. Create a document for this synthesis.
"""

				synthesis_state = {
					"messages": [
						SystemMessage(
							content="You are a synthesis agent specializing in creating comprehensive reports and deliverables. Your job is to take research findings and create polished, actionable documents with clear recommendations."
						),
						HumanMessage(content=synthesis_input),
					]
				}
				synthesis_result = await synthesis_agent.ainvoke(synthesis_state)
				execution_time = asyncio.get_event_loop().time() - start_time

				agent_output = AgentOutput(
					agent_name="Synthesis Agent",
					output_content=synthesis_result["messages"][-1].content,
					execution_time=execution_time,
					tools_used=["document_generator_tool"],
					success=True,
				)

				state.synthesis_agent_output = agent_output
				state.current_stage = "synthesis_complete"

				print(f"✅ Synthesis Agent complete in {execution_time:.2f}s")

			except Exception as e:
				error_msg = f"Synthesis agent error: {str(e)}"
				state.errors.append(error_msg)
				state.synthesis_agent_output = AgentOutput(
					agent_name="Synthesis Agent",
					output_content="",
					execution_time=0,
					success=False,
					error_message=error_msg,
				)
				state.current_stage = "synthesis_failed"

			return state

		return _synthesis_agent_node

	@property
	def finalize_output_node(self):
		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.BLOCK)
		async def _finalize_output_node(state: WorkflowState) -> WorkflowState:
			"""Final output formatting node."""
			print("📄 Finalize Output Node: Formatting final results...")

			try:
				final_output_task = self.tools_registry.get_task_by_name(
					"format_final_output"
				)(state.synthesis_agent_output, state.context)
				final_output = await final_output_task

				state.final_output = final_output
				state.workflow_complete = True
				state.current_stage = "completed"

				print("✅ Final output formatting complete")

			except Exception as e:
				state.errors.append(f"Final output formatting error: {str(e)}")
				state.current_stage = "finalization_failed"

			return state

		return _finalize_output_node

	@property
	def error_handler_node(self):
		@self.agents_manager.agents.asyncflow(flow_type=AsyncFlowType.BLOCK)
		async def _error_handler_node(state: WorkflowState) -> WorkflowState:
			"""Handle errors in the workflow."""
			print(f"❌ Error Handler: {'; '.join(state.errors)}")
			state.final_output = (
				f"Workflow failed with errors: {'; '.join(state.errors)}"
			)
			state.current_stage = "error_handled"
			return state

		return _error_handler_node
