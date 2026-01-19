import asyncio
import logging
import time
from pathlib import Path
from typing import Dict, List

import yaml
import shutil

from tests.benchmark.data_generation.schemas import BenchmarkConfig, WorkloadType
from tests.benchmark.data_generation.workload.langgraph_asyncflow import (
	langgraph_asyncflow_workload,
)
from tests.benchmark.data_generation.workload.manager import WorkloadManager
from tests.benchmark.analyse.analyse import Analyse

logger = logging.getLogger(__name__)


class FlowGenticBenchmark:
	"""Benchmark harness for FlowGentic scaling tests"""

	def __init__(self, config_path: Path = Path("tests/benchmark/config.yml")):
		self.config = self._load_config(config_path)
		self._create_directories(config_path)
		self.analyser = Analyse(self.data_dir)
		self.results: Dict[str, List[Dict]] = {}

	def _load_config(self, config_path):
		with open(config_path, "r") as file:
			return yaml.safe_load(file)

	def get_run_config(self):
		run_name = self.config["run_name"]
		run_description = self.config["run_description"]

		workload_id = self.config["workload_id"]

		environment = self.config["environment"]
		n_of_agents = int(environment["n_of_agents"])
		n_of_tool_calls = int(environment["n_of_tool_calls"])
		n_of_backend_slots = int(environment["n_of_backend_slots"])
		tool_execution_duration_time = int(environment["tool_execution_duration_time"])

		return BenchmarkConfig(
			run_name=run_name,
			run_description=run_description,
			workload_id=workload_id,
			n_of_agents=n_of_agents,
			n_of_tool_calls=n_of_tool_calls,
			n_of_backend_slots=n_of_backend_slots,
			tool_execution_duration_time=tool_execution_duration_time,
		)

	def _create_directories(self, config_path):
		"""Create output directories and initialize analyser"""
		output_dir = Path(f"tests/benchmark/results/{self.config['run_name']}")
		self.data_dir = output_dir / "data"
		config_dir = output_dir / "config"
		output_dir.mkdir(parents=True, exist_ok=True)
		self.data_dir.mkdir(parents=True, exist_ok=True)
		config_dir.mkdir(parents=True, exist_ok=True)
		shutil.copy(config_path, config_dir / "config.yml")

	async def run_workload(
		self,
		n_of_agents: int,
		n_of_tool_calls: int,
		n_of_backend_slots: int,
		tool_execution_duration_time,
	) -> float:
		# Access workload
		workload_manager = WorkloadManager(
			n_of_agents,
			n_of_tool_calls,
			n_of_backend_slots,
			tool_execution_duration_time,
		)
		workloads = workload_manager.get_agent_workloads(id="langgraph_asyncflow")

		# Initial time
		t_start = time.perf_counter()

		# Submit all tasks
		await asyncio.gather(*workloads)

		# End timing: last FlowGentic call returning
		t_end = time.perf_counter()

		makespan = t_end - t_start
		logger.debug(
			f"WORKLOAD HAD MAKESPAN: {makespan} \
				with n_agents: {n_of_agents}\
				with n_of_tool_calls: {n_of_tool_calls}\
				with n_of_backend_slots: {n_of_backend_slots}\
				with tool_execution_duration_time: {tool_execution_duration_time}\
				"
		)
		return makespan

	async def run_strong_scaling(self, config: BenchmarkConfig):
		logger.info(f"=== STRONG SCALING: {config.run_name} ===")
		logger.info(f"Config is: {config.model_dump_json(indent=4)}")

		results = []
		backend_slots_options = [2**i for i in range(config.n_of_backend_slots)]

		for backend_slots in backend_slots_options:
			logger.info(f"\n--- Testing p={backend_slots} backend slots ---")

			# Run workload
			makespan = await self.run_workload(
				n_of_agents=config.n_of_agents,
				n_of_tool_calls=config.n_of_tool_calls,
				n_of_backend_slots=backend_slots,
				tool_execution_duration_time=config.tool_execution_duration_time,
			)

			# Create result
			result = config.model_dump()
			result["makespan"] = makespan
			result["n_of_backend_slots"] = backend_slots
			logger.debug(f"Writing to logs: {result}")

			results.append(result)

		# Determine experiment name for the filename
		is_noop = config.tool_execution_duration_time == 0
		experiment_name = f"strong_scaling-{config.workload_type.value}-{'noop' if is_noop else 'not_noop'}"

		self.results[experiment_name] = results
		return results

	def save_and_analyse(self):
		"""Save results and generate plots"""
		self.analyser.save_and_plot(self.results)


async def main():
	"""Run all benchmarks"""

	benchmark = FlowGenticBenchmark()

	# 1) STRONG SCALING
	# Experiment 1
	## 1.1) Varying agent, fixed tool calls
	### 1.1.1) Not noop work
	run_config_exp1 = benchmark.get_run_config()
	await benchmark.run_strong_scaling(run_config_exp1)

	# Experiment 2
	## 1.1) Varying agent, fixed tool calls
	### 1.1.2) Noop work
	run_config_exp2 = run_config_exp1.model_copy(
		update={"tool_execution_duration_time": 0}
	)
	await benchmark.run_strong_scaling(run_config_exp2)

	# # Experiment 3
	# ## 1.2) Varying tool calls, fixed agents
	# ### 1.2.1) Not noop work
	# await benchmark.run_strong_scaling(config_strong_noop)

	# # Experiment 4
	# ## 1.2) Varying tool calls, fixed agents
	# ### 1.2.2) Noop work
	# await benchmark.run_strong_scaling(config_strong_noop)

	# Save and generate plots
	benchmark.save_and_analyse()

	logger.info("\n=== BENCHMARK COMPLETE ===")


if __name__ == "__main__":
	asyncio.run(main())
