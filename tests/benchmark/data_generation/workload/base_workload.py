from abc import ABC, abstractmethod

from flowgentic.backend_engines.base import BaseEngine
from tests.benchmark.data_generation.utils.schemas import (
	WorkloadConfig,
	BenchamarkWorkloadResult,
)


class BaseWorkload(ABC):
	def __init__(
		self,
		workload_config: WorkloadConfig,
	) -> None:
		self.n_of_backend_slots = workload_config.n_of_backend_slots
		self.n_of_agents = workload_config.n_of_agents
		self.n_of_tool_calls_per_agent = workload_config.n_of_tool_calls_per_agent
		self.tool_execution_duration_time = workload_config.tool_execution_duration_time

	@abstractmethod
	async def run(self, engine: BaseEngine) -> BenchamarkWorkloadResult:
		pass
