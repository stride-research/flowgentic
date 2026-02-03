import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

import yaml
import shutil

from tests.benchmark.data_generation.experiments.synthethic_adaptive.experiment import (
	SynthethicAdaptive,
)
from tests.benchmark.data_generation.utils.io_utils import IOUtils
from tests.benchmark.data_generation.utils.schemas import (
	BenchamarkWorkloadResult,
	BenchmarkConfig,
	BenchmarkResult,
	EngineIDs,
	WorkloadConfig,
	WorkloadResult,
	WorkloadType,
)
from tests.benchmark.data_generation.workload.base_workload import BaseWorkload
from tests.benchmark.data_generation.workload.utils.engine import resolve_engine
from tests.benchmark.data_generation.workload.langgraph import LangraphWorkload

logger = logging.getLogger(__name__)


class FlowGenticBenchmark:
	"""Benchmark harness for FlowGentic scaling tests"""

	def __init__(self, config_path: Path = Path("tests/benchmark/config.yml")):
		self.io_utils = IOUtils(config_path)
		self.benchmark_config = self.io_utils.config
		self.results: Dict[str, List[Dict]] = {}

	async def run_workload(
		self, workload_orchestrator: BaseWorkload, workload_config: WorkloadConfig
	) -> BenchamarkWorkloadResult:
		# Single workload with shared backend across all agents
		workload: BaseWorkload = workload_orchestrator(workload_config=workload_config)
		engine = resolve_engine(engine_id=workload_config.engine_id)
		results: BenchamarkWorkloadResult = workload.run(engine)

		return results

	def register_experiment(self, experiment_name: str):
		data_dir, plots_dir = self.io_utils.create_experiment_directory(
			experiment_name=experiment_name
		)
		return data_dir, plots_dir


async def main():
	"""Run all benchmarks"""

	benchmark = FlowGenticBenchmark()

	# Experiment 2
	data_dir, plots_dir = benchmark.register_experiment("syntethic_adaptive")
	syntethic_adaptive = SynthethicAdaptive(
		benchmark.benchmark_config, data_dir, plots_dir
	)
	syntethic_adaptive_results: Dict[Any, Any] = syntethic_adaptive.run_experiment()
	syntethic_adaptive.save_results(syntethic_adaptive_results)


if __name__ == "__main__":
	asyncio.run(main())
