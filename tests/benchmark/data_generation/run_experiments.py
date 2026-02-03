import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

import yaml
import shutil

from tests.benchmark.data_generation.experiments.synthethic_adaptive import (
	SynthethicAdaptive,
)
from tests.benchmark.data_generation.utils.io_utils import IOUtils
from tests.benchmark.data_generation.utils.schemas import (
	BenchamarkWorkloadResult,
	BenchmarkConfig,
	BenchmarkResult,
	EngineIDs,
	WorkloadConfig,
	WorkloadParameters,
	WorkloadResult,
	WorkloadType,
)
from tests.benchmark.data_generation.workload.base_workload import BaseWorkload
from tests.benchmark.data_generation.workload.utils.engine import resolve_engine
from tests.benchmark.data_generation.workload.langgraph import LangraphWorkload
from tests.benchmark.analyse.analyse import Analyse

logger = logging.getLogger(__name__)


class FlowGenticBenchmark:
	"""Benchmark harness for FlowGentic scaling tests"""

	def __init__(self, config_path: Path = Path("tests/benchmark/config.yml")):
		self.io_utils = IOUtils(config_path)
		self.benchmark_config = self.io_utils.config
		self.analyser = Analyse(self.io_utils.data_dir)
		self.results: Dict[str, List[Dict]] = {}

	async def run_workload(
		self, workload_orchestrator: BaseWorkload, workload_config: WorkloadConfig
	) -> BenchamarkWorkloadResult:
		# Single workload with shared backend across all agents
		workload: BaseWorkload = workload_orchestrator(workload_config=workload_config)
		engine = resolve_engine(engine_id=workload_config.engine_id)
		results: BenchamarkWorkloadResult = workload.run(engine)

		return results

	def add_experiment_results(self, experiment_id: str, experiment_data: List[Any]):
		self.results[experiment_id] = experiment_data

	def save_and_analyse(self):
		"""Save results and generate plots"""
		logger.debug(f"Results are: {self.results}")
		self.analyser.save_and_plot(self.results)


async def main():
	"""Run all benchmarks"""

	benchmark = FlowGenticBenchmark()

	# ====== TUTORIAL =======
	# **Experiment i **
	# 1) Register ur experiment to the class of this module
	# 2) Call ur experiment class and pass on the benchmark config

	# Experiment 2
	syntethic_adaptive = SynthethicAdaptive(benchmark.benchmark_config)
	syntethic_adaptive_results: Dict[str, Any] = syntethic_adaptive.run_experiment()
	benchmark.add_experiment_results("syntethic_adaptive", syntethic_adaptive_results)


if __name__ == "__main__":
	asyncio.run(main())
