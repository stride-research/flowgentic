from typing import Any, Dict

from tests.benchmark.data_generation.experiments.base.base_experiment import (
	BaseExperiment,
)
from tests.benchmark.data_generation.experiments.backend_comparison.utils.plots import (
	BackendComparisonPlotter,
)
from tests.benchmark.data_generation.utils.schemas import (
	BenchmarkConfig,
	BenchmarkedRecord,
	EngineIDs,
	WorkloadConfig,
	WorkloadResult,
)
from tests.benchmark.data_generation.workload.langgraph import LangraphWorkload

import logging


logger = logging.getLogger(__name__)

class BackendComparison(BaseExperiment):
	"""
	Compares FlowGentic performance across workflow-aware (AsyncFlow) and
	runtime-oriented (Parsl) backends using identical synthetic workloads.

	Fixed workload config (same agents, same tool calls, same slots).
	The only varable is the execution engine
	"""

	ENGINES = [EngineIDs.ASYNCFLOW.value, EngineIDs.PARSL.value]

	def __init__(
		self, benchmark_config: BenchmarkConfig, data_dir: str, plots_dir: str
	) -> None:
		super().__init__(data_dir, plots_dir)
		self.benchmark_config = benchmark_config
		self.plotter = BackendComparisonPlotter(plots_dir=plots_dir)

	async def _run_for_engine(
		self,
		config: BenchmarkConfig,
		engine_id: str,
	) -> Dict:
		"""
		Run the identical workload on a specific engine.
		"""
		logger.info(f"=== BACKEND COMPARISON: {engine_id} ===")
		logger.info(
			f"Config: {config.n_of_agents} agents, "
			f"{config.n_of_tool_calls_per_agent} tool calls/agent, "
			f"{config.n_of_backend_slots} slots, "
			f"duration={config.tool_execution_duration_time}s"
		)

		workload_config = WorkloadConfig(
			n_of_agents=config.n_of_agents,
			n_of_tool_calls_per_agent=config.n_of_tool_calls_per_agent,
			n_of_backend_slots=config.n_of_backend_slots,
			tool_execution_duration_time=config.tool_execution_duration_time,
			engine_id=engine_id,
		)

		workload_result: WorkloadResult = await self.run_workload(
			workload_orchestrator=LangraphWorkload,
			workload_config=workload_config,
		)

		record = BenchmarkedRecord(
			run_name=config.run_name,
			run_description=config.run_description,
			workload_id=config.workload_id,
			n_of_agents=config.n_of_agents,
			n_of_tool_calls_per_agent=config.n_of_tool_calls_per_agent,
			n_of_backend_slots=config.n_of_backend_slots,
			workload_type=config.workload_type,
			tool_execution_duration_time=config.tool_execution_duration_time,
			total_makespan=workload_result.total_makespan,
			events=workload_result.events,
		).model_dump(mode="json")

		record["engine_id"] = engine_id

		logger.info(
			f"Engine={engine_id}: makespan={workload_result.total_makespan:.4f}s"
		)

		return record

	async def run_experiment(self) -> Dict[Any, Any]:
		"""
		Run the same workload on both AsyncFlow and Parsl backends.
		"""
		results = {}

		for engine_id in self.ENGINES:
			results[engine_id] = await self._run_for_engine(
				self.benchmark_config, engine_id
			)

		logger.info("Backend comparison experiment complete.")
		self.store_data_to_disk(results)
		return results

	def generate_plots(self, data: Dict[Any, Any]):
		self.plotter.plot_results(data=data)
