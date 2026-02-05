import logging
import yaml
from pathlib import Path
from typing import Any, Dict, List

from tests.benchmark.data_generation.experiments.base.base_experiment import (
	BaseExperiment,
)
from tests.benchmark.data_generation.experiments.experiment_1a.utils.plots import (
	Experiment1aPlotter,
)
from tests.benchmark.data_generation.utils.schemas import (
	BenchmarkConfig,
	EngineIDs,
	WorkloadConfig,
	WorkloadResult,
)
from tests.benchmark.data_generation.workload.langgraph import LangraphWorkload

logger = logging.getLogger(__name__)

N_TOOLS = 2  # fetch_temperature, fetch_humidity — fixed by LangraphWorkload
CONFIG_PATH = Path("tests/benchmark/config.yml")


class Experiment1a(BaseExperiment):
	"""
	Fig 1a: Coordination throughput vs invocation rate (saturation curve).

	Sweeps tool_invocations to increase offered load while keeping n_agents=1.
	This reduces per-agent orchestration overhead compared to sweeping agents.
	Each ensemble_size (n_of_backend_slots) becomes one series on the plot.

	Formulas:
		total_invocations = calls_per_tool * N_TOOLS (with n_agents=1)
		offered_load      = total_invocations / tool_execution_duration_time
		throughput        = total_invocations / actual_makespan

	Expected shape: curves plateau at S / D (slots / duration).
	Larger ensemble sizes yield a higher plateau.
	"""

	def __init__(
		self, benchmark_config: BenchmarkConfig, data_dir: str, plots_dir: str
	) -> None:
		super().__init__(data_dir, plots_dir)
		self.benchmark_config = benchmark_config
		self.plotter = Experiment1aPlotter(plots_dir=plots_dir)
		self._load_experiment_config()

	def _load_experiment_config(self):
		"""Read experiment-specific sweep parameters from config.yml."""
		with open(CONFIG_PATH) as f:
			raw = yaml.safe_load(f)
		exp_cfg = raw.get("experiment_1a", {})

		self.ensemble_sizes: List[int] = exp_cfg.get("ensemble_sizes", [2, 4, 8])
		self.tool_invocations_sweep: List[int] = exp_cfg.get(
			"tool_invocations_sweep", [2, 4, 6, 8, 12, 16, 20, 24, 30, 40]
		)
		self.tool_duration: int = exp_cfg.get("tool_execution_duration_time", 2)

	async def run_experiment(self) -> Dict[Any, Any]:
		results: Dict[str, List[Dict[str, Any]]] = {}

		# Fixed: 1 agent to minimize orchestration overhead
		n_agents = 1

		for ensemble_size in self.ensemble_sizes:
			series_key = f"ensemble_size_{ensemble_size}"
			logger.info(f"=== {series_key} (n_of_backend_slots={ensemble_size}) ===")
			series_results: List[Dict[str, Any]] = []

			for total_invocations in self.tool_invocations_sweep:
				# Derive calls_per_tool from total invocations
				# total_invocations = n_agents * calls_per_tool * N_TOOLS
				# With n_agents=1: calls_per_tool = total_invocations / N_TOOLS
				assert total_invocations % N_TOOLS == 0, (
					f"total_invocations ({total_invocations}) must be divisible by N_TOOLS ({N_TOOLS})"
				)
				calls_per_tool = total_invocations // N_TOOLS

				logger.info(
					f"  total_invocations={total_invocations} (calls_per_tool={calls_per_tool})"
				)

				workload_config = WorkloadConfig(
					n_of_agents=n_agents,
					n_of_tool_calls_per_agent=calls_per_tool,
					n_of_backend_slots=ensemble_size,
					tool_execution_duration_time=self.tool_duration,
					engine_id=EngineIDs.ASYNCFLOW.value,
				)

				workload_result: WorkloadResult = await self.run_workload(
					workload_orchestrator=LangraphWorkload,
					workload_config=workload_config,
				)

				offered_load = total_invocations / self.tool_duration
				throughput = total_invocations / workload_result.total_makespan

				logger.info(
					f"    offered_load={offered_load:.2f} inv/s  "
					f"throughput={throughput:.2f} inv/s  "
					f"makespan={workload_result.total_makespan:.2f}s"
				)

				series_results.append(
					{
						"ensemble_size": ensemble_size,
						"n_agents": n_agents,
						"calls_per_tool": calls_per_tool,
						"tool_execution_duration_time": self.tool_duration,
						"total_invocations": total_invocations,
						"offered_load": offered_load,
						"throughput": throughput,
						"total_makespan": workload_result.total_makespan,
						"events": workload_result.events,
					}
				)

			results[series_key] = series_results

		return results

	def generate_plots(self, data: Dict[Any, Any]):
		self.plotter.plot_results(data)
