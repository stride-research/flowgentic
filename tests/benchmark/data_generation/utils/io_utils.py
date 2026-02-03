from pathlib import Path
import shutil
import yaml

from tests.benchmark.data_generation.utils.schemas import BenchmarkConfig


class IOUtils:
	def __init__(self, config_path: Path = Path("tests/benchmark/config.yml")) -> None:
		self.config = self._load_config(config_path)
		self._create_directories(config_path)

	def _load_config(self, config_path):
		with open(config_path, "r") as file:
			return yaml.safe_load(file)

	def get_run_config(self):
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

	def _create_directories(self, config_path):
		"""Create output directories and initialize analyser"""
		output_dir = Path(f"tests/benchmark/results/{self.config['run_name']}")
		self.data_dir = output_dir / "data"
		config_dir = output_dir / "config"
		output_dir.mkdir(parents=True, exist_ok=True)
		self.data_dir.mkdir(parents=True, exist_ok=True)
		config_dir.mkdir(parents=True, exist_ok=True)
		shutil.copy(config_path, config_dir / "config.yml")
