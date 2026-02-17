import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import yaml
import shutil

from tests.benchmark.data_generation.experiments.base.base_experiment import (
	BaseExperiment,
)
from tests.benchmark.data_generation.experiments.throughput_saturation.main import (
	ThroughputSaturation,
)
from tests.benchmark.data_generation.experiments.synthethic_adaptive.main import (
	SynthethicAdaptive,
)
from tests.benchmark.data_generation.utils.io_utils import IOUtils
from tests.benchmark.data_generation.utils.schemas import (
	BenchmarkConfig,
	EngineIDs,
	WorkloadConfig,
	WorkloadResult,
	WorkloadType,
)
from tests.benchmark.data_generation.workload.base_workload import BaseWorkload
from tests.benchmark.data_generation.workload.utils.engine import resolve_engine
from tests.benchmark.data_generation.workload.langgraph import LangraphWorkload

from tests.benchmark.data_generation.utils.io_utils import DiscordNotifier


logger = logging.getLogger(__name__)


class FlowGenticBenchmarkManager:
	"""Benchmark harness for FlowGentic scaling tests"""

	def __init__(self, config_path: Path = Path("tests/benchmark/config.yml")):
		self.io_utils = IOUtils(config_path)
		self.benchmark_config = self.io_utils.benchmark_config
		self.results: Dict[str, List[Dict]] = {}
		self.experiments: Dict[str, BaseExperiment] = {}

	def register_experiment(
		self, experiment_name: str, experiment_class: BaseExperiment
	):
		data_dir, plots_dir = self.io_utils.create_experiment_directory(
			experiment_name=experiment_name
		)
		self.experiments[experiment_name] = {
			"experiment_class": experiment_class,
			"data_dir": data_dir,
			"plots_dir": plots_dir,
		}
		return data_dir, plots_dir

	async def run_registerd_experiments(self):
		for experiment_name, experiment_metadata in self.experiments.items():
			started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
			config_json = self.benchmark_config.model_dump_json(indent=2)
			msg = (
				f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
				f"🚀 **Starting experiment**\n"
				f"**Experiment:** `{experiment_name}`\n"
				f"**Started at:** `{started_at}`\n"
				f"**Config:**\n```json\n{config_json}\n```"
			)
			DiscordNotifier().send_discord_notification(msg=msg)
			experiment_class = experiment_metadata.get("experiment_class")
			data_dir = experiment_metadata.get("data_dir")
			plots_dir = experiment_metadata.get("plots_dir")
			experiment_instance: BaseExperiment = experiment_class(
				self.benchmark_config, data_dir, plots_dir
			)
			# Run experiment (writes to disk incrementally)
			await experiment_instance.run_experiment()
			# Read from disk and generate plots
			experiment_instance.finalize()


async def main():
	"""Run all benchmarks"""

	benchmark = FlowGenticBenchmarkManager()

	# Throughput saturation experiment
	benchmark.register_experiment("throughput_saturation", ThroughputSaturation)

	# Experiment 2
	benchmark.register_experiment("syntethic_adaptive", SynthethicAdaptive)

	# Execution of experiments
	await benchmark.run_registerd_experiments()


if __name__ == "__main__":
	asyncio.run(main())
