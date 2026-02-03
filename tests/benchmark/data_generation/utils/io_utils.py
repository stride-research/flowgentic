from pathlib import Path
import shutil
import yaml

from tests.benchmark.data_generation.utils.schemas import BenchmarkConfig


class IOUtils:
	def __init__(self, config_path: Path = Path("tests/benchmark/config.yml")) -> None:
		self.config = self._load_config(config_path)
		self.config_path = config_path
		self.benchmark_config = self.get_run_config()
		self._create_core_directories(config_path)

	def _load_config(self, config_path):
		with open(config_path, "r") as file:
			return yaml.safe_load(file)

	def get_run_config(self):
		"""Parses the config.yml"""
		run_name = self.config["run_name"]
		run_description = self.config["run_description"]

		workload_id = self.config["workload_id"]

		environment = self.config["environment"]
		n_of_agents = int(environment["n_of_agents"])
		n_of_tool_calls_per_agent = int(environment["n_of_tool_calls_per_agent"])
		n_of_backend_slots = int(environment["n_of_backend_slots"])
		tool_execution_duration_time = int(environment["tool_execution_duration_time"])

		return BenchmarkConfig(
			run_name=run_name,
			run_description=run_description,
			workload_id=workload_id,
			n_of_agents=n_of_agents,
			n_of_tool_calls_per_agent=n_of_tool_calls_per_agent,
			n_of_backend_slots=n_of_backend_slots,
			tool_execution_duration_time=tool_execution_duration_time,
		)

	def _create_core_directories(self, run_configuration_name: str):
		"""Create output directories and initialize analyser

		Ideal map:
			- results
				- {run_configuration_name}
					- results
						- {experiment_name}
							- data (json stuff)
								- temp.json
								- foo.sjon
							- plots (the actual plots)
						- config
							- config.yml
		"""
		# 1) Define the paths
		output_dir = Path(f"tests/benchmark/results/{run_configuration_name}")
		config_dir = output_dir / "config"

		# Create folders
		output_dir.mkdir(parents=True, exist_ok=True)
		shutil.copy(
			self.config_path, config_dir / "config.yml"
		)  # copy config for reproducibility

	def _create_experiment_directory(self, experiment_name: str): ...
