from pathlib import Path
import shutil
import yaml

from tests.benchmark.data_generation.utils.schemas import BenchmarkConfig


class IOUtils:
	def __init__(self, config_path: Path = Path("tests/benchmark/config.yml")) -> None:
		self.config_path = config_path
		self.benchmark_config = self.get_run_config()
		self._create_core_directories(self.benchmark_config.run_name)

	def _load_config(self, config_path):
		with open(config_path, "r") as file:
			return yaml.safe_load(file)

	def get_run_config(self):
		"""Parses the config.yml"""
		# Read the yaml
		config_yaml = self._load_config(self.config_path)

		# Create object out of config
		run_name = config_yaml["run_name"]
		run_description = config_yaml["run_description"]

		workload_id = config_yaml["workload_id"]

		environment = config_yaml["environment"]
		n_of_agents = int(environment["n_of_agents"])
		n_of_tool_calls_per_agent = int(environment["n_of_tool_calls_per_agent"])
		n_of_backend_slots = int(environment["n_of_backend_slots"])
		tool_execution_duration_time = int(environment["tool_execution_duration_time"])
		experiments = config_yaml.get("experiments", {})
		
		return BenchmarkConfig(
			run_name=run_name,
			run_description=run_description,
			workload_id=workload_id,
			n_of_agents=n_of_agents,
			n_of_tool_calls_per_agent=n_of_tool_calls_per_agent,
			n_of_backend_slots=n_of_backend_slots,
			tool_execution_duration_time=tool_execution_duration_time,
			experiments=experiments,
		)

	def _create_core_directories(self, run_configuration_name: str):
		"""Create output directories and initialize analyser

		Ideal map:
			- results
				- {run_configuration_name}
					- {experiment_name}
						- data (json stuff)
							- temp.json
							- foo.sjon
						- plots (the actual plots)
					- config
						- config.yml
		"""
		# Define the paths
		output_dir = Path(f"tests/benchmark/results/{run_configuration_name}")
		self.output_dir = output_dir
		config_dir = output_dir / "config"

		# Create folders
		output_dir.mkdir(parents=True, exist_ok=True)
		config_dir.mkdir(parents=True, exist_ok=True)
		shutil.copy(
			self.config_path, config_dir / "config.yml"
		)  # copy config for reproducibility

	def create_experiment_directory(self, experiment_name: str):
		experiment_dir = self.output_dir / "experiments" / experiment_name
		data_dir = experiment_dir / "data"
		plots_dir = experiment_dir / "plots"

		experiment_dir.mkdir(parents=True, exist_ok=True)
		data_dir.mkdir(parents=True, exist_ok=True)
		plots_dir.mkdir(parents=True, exist_ok=True)

		return data_dir, plots_dir
