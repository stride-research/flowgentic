from flowgentic.core.models.implementations.dummyProvider import DummyModelProvider
from tests.benchmark.data_generation.workload.langgraph_asyncflow import (
	langgraph_asyncflow_workload,
)


class WorkloadManager:
	def __init__(
		self,
		n_of_agents: int,
		n_of_tool_calls: int,
		n_of_backend_slots: int,
		tool_execution_duration_time,
	) -> None:
		self.n_of_agents = n_of_agents
		self.n_of_tool_calls = n_of_tool_calls
		self.n_of_backend_slots = n_of_backend_slots
		self.tool_execution_duration_time = tool_execution_duration_time

		self.reasoner_model = DummyModelProvider(
			model_id="dummy/dummy_moodel",
			tool_names=["fetch_temperature"],
			n_of_tool_calls=n_of_tool_calls,
		)

	def get_shared_backend_workload(self, id):
		"""
		Returns a single workload with SHARED backend across all agents.
		This allows backend slots to parallelize setup work.
		"""
		print(f"NUMBER OF AGENTS: {self.n_of_agents}")
		if id == "langgraph_asyncflow":
			return langgraph_asyncflow_workload(
				reasoner_model=self.reasoner_model,
				n_of_backend_slots=self.n_of_backend_slots,
				n_of_agents=self.n_of_agents,
				tool_execution_duration_time=self.tool_execution_duration_time,
			)
		raise ValueError(f"Unknown workload id: {id}")
