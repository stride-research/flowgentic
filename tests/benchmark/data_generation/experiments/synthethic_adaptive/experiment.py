from typing import Any, Dict
from tests.benchmark.data_generation.experiments.base.base_experiment import (
	BaseExperiment,
)
from tests.benchmark.data_generation.experiments.base.base_plots import BasePlotter
from tests.benchmark.data_generation.experiments.synthethic_adaptive.plots import (
	SyntheticAdaptivePlotter,
)
from tests.benchmark.data_generation.utils.schemas import BenchmarkConfig

import logging

logger = logging.getLogger(__name__)


class SynthethicAdaptive(BaseExperiment):
	def __init__(
		self, benchmark_config: BenchmarkConfig, data_dir: str, plots_dir: str
	) -> None:
		self.benchmark_config = benchmark_config
		self.plotter = SyntheticAdaptivePlotter()
		super().__init__(data_dir, plots_dir)

	# ""async def run_strong_scaling(self, config: BenchmarkConfig):
	# 	"""
	# 	Strong scaling test with shared backend across all agents.
	# 	This properly measures parallelization of flowgentic setup overhead.
	# 	"""
	# 	logger.info(f"=== STRONG SCALING: {config.run_name} ===")
	# 	logger.info(f"Config is: {config.model_dump_json(indent=4)}")

	# 	worklaods_results = []
	# 	backend_slots_options = [2**i for i in range(config.n_of_backend_slots + 1)]

	# 	for backend_slots in backend_slots_options:
	# 		logger.info(f"\n--- Testing p={backend_slots} backend slots ---")

	# 		workload_orchestrator = LangraphWorkload
	# 		workload_config = WorkloadConfig(
	# 			n_of_agents=config.n_of_agents,
	# 			n_of_tool_calls_per_agent=config.n_of_tool_calls_per_agent,
	# 			n_of_backend_slots=config.n_of_backend_slots,
	# 			tool_execution_duration_time=config.tool_execution_duration_time,
	# 			engine_id=EngineIDs.ASYNCFLOW.value,
	# 		)

	# 		workload_result = await self.run_workload(
	# 			workload_orchestrator=workload_orchestrator,
	# 			workload_config=workload_config,
	# 		)

	# 		benchmark_result = BenchmarkResult(
	# 			# Metadata
	# 			run_name=config.run_name,
	# 			run_description=config.run_description,
	# 			workload_id=config.workload_id,
	# 			n_of_agents=config.n_of_agents,
	# 			n_of_tool_calls_per_agent=config.n_of_tool_calls_per_agent,
	# 			n_of_backend_slots=backend_slots,
	# 			workload_type=config.workload_type,
	# 			tool_execution_duration_time=config.tool_execution_duration_time,
	# 			# Results
	# 			total_makespan=workload_result.total_makespan,
	# 			total_flowgentic_makespan=workload_result.total_flowgentic_makespan,
	# 		).model_dump()
	# 		logger.debug(f"Writing to logs: {benchmark_result}")

	# 		worklaods_results.append(benchmark_result)

	# 	is_noop = config.tool_execution_duration_time == 0
	# 	experiment_name = f"strong_scaling-{config.workload_type.value}-{'noop' if is_noop else 'not_noop'}"

	# 	self.results[experiment_name] = worklaods_results
	# 	return worklaods_results

	# # async def run_weak_scaling(self, config: BenchmarkConfig):
	# # 	logger.info(f"=== WEAK SCALING: {config.run_name} ===")
	# # 	logger.info(f"Config is: {config.model_dump_json(indent=4)}")

	# # 	results = []
	# # 	backend_slots_options = [2**i for i in range(config.n_of_backend_slots + 1)]

	# # 	for backend_slots in backend_slots_options:
	# # 		logger.info(f"\n--- Testing p={backend_slots} backend slots ---")

	# # 		n_of_tool_calls_per_agent = config.n_of_agents * backend_slots

	# # 		# Run workload

	# # 		makespan, total_flowgentic_makespan, total_execution_time = await self.run_workload(
	# # 			n_of_agents=config.n_of_agents,
	# # 			n_of_tool_calls_per_agent=n_of_tool_calls_per_agent,  # Hardcode for experiments 3, 4
	# # 			n_of_backend_slots=backend_slots,
	# # 			tool_execution_duration_time=config.tool_execution_duration_time,
	# # 		)

	# # 		# Create result
	# # 		result = BenchmarkResult(
	# # 			run_name=config.run_name,
	# # 			run_description=config.run_description,
	# # 			workload_id=config.workload_id,
	# # 			n_of_agents=config.n_of_agents,
	# # 			n_of_tool_calls_per_agent=n_of_tool_calls_per_agent,
	# # 			n_of_backend_slots=backend_slots,
	# # 			workload_type=config.workload_type,
	# # 			tool_execution_duration_time=config.tool_execution_duration_time,
	# # 			total_makespan=makespan,
	# # 			total_flowgentic_makespan=total_flowgentic_makespan,
	# # 		).model_dump()
	# # 		logger.debug(f"Writing to logs: {result}")
	# # 		results.append(result)

	# # 	# Determine experiment name for the filename
	# # 	is_noop = config.tool_execution_duration_time == 0
	# # 	experiment_name = f"weak_scaling-{config.workload_type.value}-{'noop' if is_noop else 'not_noop'}"

	# # 	self.results[experiment_name] = results
	# # 	return results

	# def run_experiment(self):
	# 	# 1) STRONG SCALING
	# 	## 1.1) Varying agent, fixed tool calls

	# 	# Experiment 1: Real work (with tool execution time)
	# 	run_config_exp1 = benchmark.get_run_config()
	# 	await benchmark.run_strong_scaling(run_config_exp1)

	# 	# # Experiment 2: Noop work (overhead only)
	# 	# run_config_exp2 = run_config_exp1.model_copy(
	# 	# 	update={"tool_execution_duration_time": 0}
	# 	# )
	# 	# await benchmark.run_strong_scaling(run_config_exp2)

	# 	# # 2) WEAK SCALING (commented out)
	# 	# # ## 2.1) Varying tool calls, fixed agents ; for each agent do m x p tool calls

	# 	# # Experiment 3: Not noop work
	# 	# await benchmark.run_weak_scaling(run_config_exp1)

	# 	# # Experiment 4: Noop work
	# 	# await benchmark.run_weak_scaling(run_config_exp2)""

	def run_experiment(
		self,
	) -> Dict[Any, Any]:  # Data expected to come out format is meant to be JSON-like
		return {
			"OUTPUT": {
				"subexperiment1": 1,
				"subexperiment2": 2,
			}
		}

	def generate_plots(self, data: Dict[Any, Any]):
		self.plotter.plot_results(data=data)
