import asyncio
import json
from typing import List, Tuple, Dict, Any
from openai.types.chat import ChatCompletionMessageFunctionToolCall
from radical.asyncflow import WorkflowEngine
from flowgentic.backend_engines.base import BaseEngine
from flowgentic.core.tool.tool import Tool


class AsyncFlowEngine(BaseEngine):
	def __init__(self, flow: WorkflowEngine):
		self.flow = flow
		self._task_registry = {}

	async def execute_tools(
		self,
		tools_to_use: List[ChatCompletionMessageFunctionToolCall],
		tools: Dict[str, Tool],
	) -> Dict[str, Any]:
		futures = []
		task_names = []

		for tool in tools_to_use:
			func = tool.function
			if func.name not in self._task_registry:
				self._task_registry[func.name] = self.flow.function_task(
					tools[func.name].func,
					service=tools[func.name].config.get("run_as_service"),
				)

			task = self._task_registry[func.name]
			futures.append(task(**json.loads(func.arguments)))
			task_names.append(func.name)

		# Concurrent execution
		results = await asyncio.gather(*futures)
		return dict(zip(task_names, results))

	async def wrap_agent_run(self, agent_logic_fn, *args, **kwargs):
		"""
		Wraps the agent loop in an AsyncFlow block.
		This allows the entire agent 'thought' to be a managed unit on the HPC.
		"""

		@self.flow.block
		async def agent_block(*fn_args, **fn_kwargs):
			return await agent_logic_fn(*fn_args, **fn_kwargs)

		# Trigger the block and return the future
		return await agent_block(*args, **kwargs)
