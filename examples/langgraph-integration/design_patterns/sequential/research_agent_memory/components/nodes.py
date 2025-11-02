"""
Memory-aware workflow nodes for sequential pattern.

This module defines all workflow nodes that leverage memory capabilities
to maintain context across stages and provide more coherent, context-aware responses.
"""

import asyncio
from typing import Dict
from flowgentic.langGraph.main import LangraphIntegration
from flowgentic.langGraph.memory import MemoryManager
from ..utils.schemas import WorkflowState, AgentOutput, MemoryStats
from .utils.actions_registry import ActionsRegistry
from flowgentic.langGraph.execution_wrappers import AsyncFlowType
from flowgentic.utils.llm_providers import ChatLLMProvider
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class WorkflowNodes:
	"""Memory-aware workflow nodes with access to memory manager."""

	def __init__(
		self,
		agents_manager: LangraphIntegration,
		tools_registry: ActionsRegistry,
		memory_manager: MemoryManager,
	) -> None:
		self.agents_manager = agents_manager
		self.tools_registry = tools_registry
		self.memory_manager = memory_manager

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
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _preprocess_node(state: WorkflowState) -> WorkflowState:
			"""Memory-aware preprocessing node."""
			print(
				"🔄 Preprocessing Node (Memory-Enabled): Starting input validation..."
			)

			try:
				# Validate input
				validation_task = self.tools_registry.get_function_task_by_name(
					"validate_input"
				)(state.user_input)
				validation_data = await validation_task

				state.validation_data = validation_data
				state.preprocessing_complete = True
				state.current_stage = "preprocessing_complete"

				# Add preprocessing result to memory
				preprocessing_message = AIMessage(
					content=f"Input validated: {validation_data.word_count} words, domain: {validation_data.metadata.get('domain')}"
				)
				await self.memory_manager.add_interaction(
					user_id=state.user_id, messages=[preprocessing_message]
				)

				# Update memory stats
				memory_stats = self.memory_manager.get_memory_health()
				state.memory_stats = MemoryStats(**memory_stats)
				state.memory_operations.append("preprocessing_memory_update")

				print(
					f"✅ Preprocessing complete: {validation_data.word_count} words, domain: {validation_data.metadata.get('domain')}"
				)
				print(f"📊 Memory: {memory_stats['total_messages']} messages stored")

			except Exception as e:
				state.errors.append(f"Preprocessing error: {str(e)}")
				state.current_stage = "preprocessing_failed"

			return state

		return _preprocess_node

	@property
	def research_agent_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _research_agent_node(state: WorkflowState) -> WorkflowState:
			"""Memory-aware research agent node."""
			print("🔍 Research Agent Node (Memory-Enabled): Starting research...")

			try:
				start_time = asyncio.get_event_loop().time()

				# Get relevant context from memory
				memory_context = await self.memory_manager.get_relevant_context(
					user_id=state.user_id, query="research renewable energy storage"
				)
				relevant_messages = memory_context.get("relevant_messages", [])

				print(
					f"🧠 Retrieved {len(relevant_messages)} relevant messages from memory"
				)

				# Get tools
				tools = [
					self.tools_registry.get_tool_by_name("web_search"),
					self.tools_registry.get_tool_by_name("data_analysis"),
				]

				# Create research agent
				research_agent = create_react_agent(
					model=ChatLLMProvider(
						provider="OpenRouter", model="google/gemini-2.5-flash"
					),
					tools=tools,
				)

				# Build context-aware system message
				memory_context_str = "\n".join(
					[
						f"- {msg.content[:100]}..."
						for msg in relevant_messages[-3:]
						if hasattr(msg, "content")
					]
				)

				system_message = SystemMessage(
					content=f"""You are a research agent specializing in renewable energy and technology analysis.
					
Previous context from memory:
{memory_context_str if memory_context_str else "No previous context"}

Your task: Conduct comprehensive research on the user's query, leveraging any relevant previous context."""
				)

				# Execute research
				research_state = {
					"messages": [system_message, HumanMessage(content=state.user_input)]
				}

				result = await research_agent.ainvoke(research_state)
				end_time = asyncio.get_event_loop().time()

				# Extract research output
				research_messages = result.get("messages", [])
				final_message = research_messages[-1] if research_messages else None

				if final_message and hasattr(final_message, "content"):
					output_content = final_message.content
				else:
					output_content = "Research completed with tool usage"

				# Store in state
				state.research_agent_output = AgentOutput(
					agent_name="Research Agent",
					output_content=output_content,
					execution_time=end_time - start_time,
					tools_used=["web_search", "data_analysis"],
					success=True,
				)

				# Add to memory
				await self.memory_manager.add_interaction(
					user_id=state.user_id, messages=research_messages
				)

				# Update memory context
				state.memory_context = memory_context
				state.memory_operations.append("research_memory_update")

				# Update state messages
				state.messages.extend(research_messages)
				state.current_stage = "research_complete"

				print(f"✅ Research complete (took {end_time - start_time:.2f}s)")
				print(
					f"📊 Memory now contains {self.memory_manager.get_memory_health()['total_messages']} messages"
				)

			except Exception as e:
				logger.error(f"Research agent error: {str(e)}")
				state.errors.append(f"Research error: {str(e)}")
				state.current_stage = "research_failed"

			return state

		return _research_agent_node

	@property
	def context_preparation_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _context_preparation_node(state: WorkflowState) -> WorkflowState:
			"""Memory-aware context preparation node."""
			print(
				"🔧 Context Preparation Node (Memory-Enabled): Preparing synthesis context..."
			)

			try:
				# Get memory context for synthesis
				memory_context = await self.memory_manager.get_relevant_context(
					user_id=state.user_id, query="synthesis recommendations"
				)

				# Prepare synthesis context using memory
				if state.research_agent_output:
					prepare_context_task = (
						self.tools_registry.get_function_task_by_name(
							"prepare_synthesis_context"
						)(state.research_agent_output.output_content, memory_context)
					)

					context_data = await prepare_context_task

					print(
						f"🧠 Context prepared with {context_data.get('memory_message_count', 0)} memory messages"
					)
					print(
						f"📊 Memory efficiency: {context_data.get('memory_efficiency', 0):.1%}"
					)

					state.memory_operations.append("context_prep_memory_query")

				state.current_stage = "context_prepared"
				print("✅ Context preparation complete")

			except Exception as e:
				logger.error(f"Context preparation error: {str(e)}")
				state.errors.append(f"Context preparation error: {str(e)}")

			return state

		return _context_preparation_node

	@property
	def synthesis_agent_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _synthesis_agent_node(state: WorkflowState) -> WorkflowState:
			"""Memory-aware synthesis agent node."""
			print("🏗️ Synthesis Agent Node (Memory-Enabled): Creating deliverables...")

			try:
				start_time = asyncio.get_event_loop().time()

				# Get comprehensive memory context
				memory_context = await self.memory_manager.get_relevant_context(
					user_id=state.user_id,
					query="all research findings and recommendations",
				)
				relevant_messages = memory_context.get("relevant_messages", [])

				print(
					f"🧠 Synthesizing with {len(relevant_messages)} relevant memory items"
				)

				# Get tools
				tools = [
					self.tools_registry.get_tool_by_name("document_generator"),
					self.tools_registry.get_tool_by_name("recommendation_engine"),
				]

				# Create synthesis agent
				synthesis_agent = create_react_agent(
					model=ChatLLMProvider(
						provider="OpenRouter", model="google/gemini-2.5-flash"
					),
					tools=tools,
				)

				# Build memory-informed system message
				memory_summary = "\n".join(
					[
						f"- {msg.content[:150]}..."
						for msg in relevant_messages
						if hasattr(msg, "content")
					]
				)

				system_message = SystemMessage(
					content=f"""You are a synthesis agent creating comprehensive reports and recommendations.

Memory Context (previous findings):
{memory_summary if memory_summary else "No previous findings"}

Your task: Synthesize all research into actionable recommendations, building on all previous context."""
				)

				# Execute synthesis
				synthesis_input = f"""Based on the research conducted, create a comprehensive report with:
1. Executive summary of findings
2. Key insights and trends
3. Actionable recommendations
4. Implementation roadmap

Original query: {state.user_input}"""

				synthesis_state = {
					"messages": [system_message, HumanMessage(content=synthesis_input)]
				}

				result = await synthesis_agent.ainvoke(synthesis_state)
				end_time = asyncio.get_event_loop().time()

				# Extract synthesis output
				synthesis_messages = result.get("messages", [])
				final_message = synthesis_messages[-1] if synthesis_messages else None

				if final_message and hasattr(final_message, "content"):
					output_content = final_message.content
				else:
					output_content = "Synthesis completed with recommendations"

				# Store in state
				state.synthesis_agent_output = AgentOutput(
					agent_name="Synthesis Agent",
					output_content=output_content,
					execution_time=end_time - start_time,
					tools_used=["document_generator", "recommendation_engine"],
					success=True,
				)

				# Add to memory
				await self.memory_manager.add_interaction(
					user_id=state.user_id, messages=synthesis_messages
				)

				# Update state
				state.messages.extend(synthesis_messages)
				state.memory_operations.append("synthesis_memory_update")
				state.current_stage = "synthesis_complete"

				print(f"✅ Synthesis complete (took {end_time - start_time:.2f}s)")

			except Exception as e:
				logger.error(f"Synthesis agent error: {str(e)}")
				state.errors.append(f"Synthesis error: {str(e)}")
				state.current_stage = "synthesis_failed"

			return state

		return _synthesis_agent_node

	@property
	def finalize_output_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _finalize_output_node(state: WorkflowState) -> WorkflowState:
			"""Finalize output with memory statistics."""
			print(
				"📄 Finalize Output Node (Memory-Enabled): Formatting final results..."
			)

			try:
				if state.synthesis_agent_output:
					# Get final memory health
					memory_health = self.memory_manager.get_memory_health()

					# Format output with memory stats
					format_task = self.tools_registry.get_function_task_by_name(
						"format_final_output"
					)(state.synthesis_agent_output.output_content, memory_health)

					state.final_output = await format_task
					state.workflow_complete = True
					state.current_stage = "completed"

					print("✅ Final output formatting complete")
					print(f"\n🧠 Final Memory Statistics:")
					print(
						f"   - Total messages: {memory_health.get('total_messages', 0)}"
					)
					print(
						f"   - Memory efficiency: {memory_health.get('memory_efficiency', 0):.1%}"
					)
					print(
						f"   - Average importance: {memory_health.get('average_importance', 0):.2f}"
					)
					print(f"   - Operations performed: {len(state.memory_operations)}")

			except Exception as e:
				logger.error(f"Finalization error: {str(e)}")
				state.errors.append(f"Finalization error: {str(e)}")

			return state

		return _finalize_output_node

	@property
	def error_handler_node(self):
		@self.agents_manager.execution_wrappers.asyncflow(
			flow_type=AsyncFlowType.EXECUTION_BLOCK
		)
		async def _error_handler_node(state: WorkflowState) -> WorkflowState:
			"""Handle errors with memory context."""
			print("❌ Error Handler Node: Processing errors...")

			error_summary = "\n".join(f"- {error}" for error in state.errors)
			state.final_output = f"""
Workflow encountered errors:
{error_summary}

Current stage: {state.current_stage}
Memory operations completed: {len(state.memory_operations)}
"""
			state.workflow_complete = True
			return state

		return _error_handler_node
