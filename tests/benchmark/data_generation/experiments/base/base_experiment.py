from abc import ABC, abstractmethod
import json
from typing import Any, Dict, List
import logging

from tests.benchmark.data_generation.utils.schemas import WorkloadConfig, WorkloadResult
from tests.benchmark.data_generation.workload.base_workload import BaseWorkload
from tests.benchmark.data_generation.workload.utils.engine import resolve_engine

logger = logging.getLogger(__name__)


class BaseExperiment(ABC):
	"""
	Each experiment is responsible of:
		1) Generating experiment data (run_experiment)
		2) Store results that data (store_results)
		3) Generating plot (generate_plots)

	"""

	def __init__(self, data_dir, plots_dir) -> None:
		super().__init__()
		self.data_dir = data_dir
		self.plots_dir = plots_dir

	async def run_workload(
		self, workload_orchestrator: BaseWorkload, workload_config: WorkloadConfig
	) -> WorkloadResult:
		# Event collector for profiling
		events: List[Dict[str, Any]] = []

		# Single workload with shared backend across all agents
		workload: BaseWorkload = workload_orchestrator(workload_config=workload_config)
		engine = await resolve_engine(
			engine_id=workload_config.engine_id,
			n_of_backend_slots=workload_config.n_of_backend_slots,
			observer=events.append,  # Simple observer: just append to list
		)
		makespan = await workload.run(engine)

		return WorkloadResult(total_makespan=makespan, events=events)

	@abstractmethod
	async def run_experiment(
		self,
	) -> Dict[Any, Any]:  # Data expected to come out format is meant to be JSON-like
		pass

	@abstractmethod
	def generate_plots(self, data: Dict[Any, Any]):
		pass

	def save_results(
		self, data: Dict[Any, Any]
	):  # Data expected to come in format is meant to be JSON-like
		self.store_data_to_disk(data)
		self.generate_plots(data)

	def store_data_to_disk(self, data: Dict[Any, Any]):
		"""Store results to disk
		1. Create a file in the corresponding folder (self.experiment_path_dir)
		"""
		with open(self.data_dir / "data.json", "w") as f:
			json.dump(data, f, indent=2)
		logger.info(f"✓ Results saved to {self.data_dir}")
