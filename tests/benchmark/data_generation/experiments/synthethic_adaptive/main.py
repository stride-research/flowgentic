from enum import Enum
from typing import Any, Callable, Dict, Literal

from tests.benchmark.data_generation.experiments.base.base_experiment import (
	BaseExperiment,
)
from tests.benchmark.data_generation.experiments.base.base_plots import BasePlotter
from tests.benchmark.data_generation.experiments.synthethic_adaptive.utils.plots import (
	SyntheticAdaptivePlotter,
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


ScalingType = Literal["strong", "weak"]


class SynthethicAdaptive(BaseExperiment):
	def __init__(
		self, benchmark_config: BenchmarkConfig, data_dir: str, plots_dir: str
	) -> None:
		super().__init__(data_dir, plots_dir)
		self.benchmark_config = benchmark_config
		self.plotter = SyntheticAdaptivePlotter(plots_dir=plots_dir)
		self.results: Dict[str, Any] = {}  # {experiment_name: results}

	async def _run_scaling_experiment(
		self,
		config: BenchmarkConfig,
		scaling_type: ScalingType,
		experiment_name: str,
	) -> None:
		"""
		Generic scaling experiment runner.

		Args:
			config: Benchmark configuration
			scaling_type: "strong" (fixed workload) or "weak" (workload scales with p)
			experiment_name: Key to store results under in self.results
		"""
		scaling_label = scaling_type.upper()
		logger.info(f"=== {scaling_label} SCALING: {config.run_name} ===")
		logger.info(f"Config is: {config.model_dump_json(indent=4)}")

		workloads_results = []
		backend_slots_options = [2**i for i in range(config.n_of_backend_slots + 1)]

		for backend_slots in backend_slots_options:
			logger.info(f"\n--- Testing p={backend_slots} backend slots ---")

			# Key difference: how n_of_tool_calls_per_agent is calculated
			if scaling_type == "strong":
				# Strong scaling: fixed workload
				n_tool_calls = config.n_of_tool_calls_per_agent
			else:
				# Weak scaling: workload scales with backend slots
				n_tool_calls = config.n_of_tool_calls_per_agent * backend_slots

			workload_config = WorkloadConfig(
				n_of_agents=config.n_of_agents,
				n_of_tool_calls_per_agent=n_tool_calls,
				n_of_backend_slots=backend_slots,
				tool_execution_duration_time=config.tool_execution_duration_time,
				engine_id=EngineIDs.ASYNCFLOW.value,
			)

			workload_result: WorkloadResult = await self.run_workload(
				workload_orchestrator=LangraphWorkload,
				workload_config=workload_config,
			)
			logger.debug(f"Workload result is: {workload_result}")

			benchmark_result = BenchmarkedRecord(
				# Metadata
				run_name=config.run_name,
				run_description=config.run_description,
				workload_id=config.workload_id,
				n_of_agents=config.n_of_agents,
				n_of_tool_calls_per_agent=n_tool_calls,
				n_of_backend_slots=backend_slots,
				workload_type=config.workload_type,
				tool_execution_duration_time=config.tool_execution_duration_time,
				# Results
				total_makespan=workload_result.total_makespan,
				events=workload_result.events,
			).model_dump(mode="json")
			logger.debug(f"Writing to logs: {benchmark_result}")

			workloads_results.append(benchmark_result)

			# Write to disk after each iteration (incremental save)
			self.results[experiment_name] = workloads_results
			self.store_data_to_disk(self.results)

	async def run_strong_scaling(self, config: BenchmarkConfig) -> None:
		"""
		Strong scaling test: fixed workload, increasing backend slots.
		Measures parallelization efficiency.
		"""
		is_noop = config.tool_execution_duration_time == 0
		experiment_name = f"strong_scaling-{'noop' if is_noop else 'op'}-work"
		await self._run_scaling_experiment(config, "strong", experiment_name)

	async def run_weak_scaling(self, config: BenchmarkConfig) -> None:
		"""
		Weak scaling test: workload scales proportionally with backend slots.
		n_of_tool_calls_per_agent = base_tool_calls * backend_slots
		"""
		is_noop = config.tool_execution_duration_time == 0
		experiment_name = f"weak_scaling-{'noop' if is_noop else 'op'}-work"
		await self._run_scaling_experiment(config, "weak", experiment_name)

	async def run_experiment(self) -> None:
		"""Run experiment. Data is written to disk incrementally."""
		# 1) STRONG SCALING: Fixed workload, varying backend slots
		await self.run_strong_scaling(self.benchmark_config)

		# 2) WEAK SCALING: Workload scales with backend slots (tool_calls * p)
		await self.run_weak_scaling(self.benchmark_config)

	def generate_plots(self, data: Dict[Any, Any]):
		self.plotter.plot_results(data=data)
