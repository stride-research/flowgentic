from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseExperiment(ABC):
	"""
	Each experiment is responsible of:
		1) Generating experiment data (run_experiment)
		2) Store results that data (store_results)
		3) Generating plot (generate_plots)

	"""

	def __init__(self, experiment_path_dir) -> None:
		super().__init__()
		self.experiment_path_dir = experiment_path_dir

	@abstractmethod
	def run_experiment(
		self,
	) -> Dict[Any]:  # Data expected to come out format is meant to be JSON-like
		pass

	@abstractmethod
	def store_results(self, data: Dict[Any]):
		"""Store results to disk
		1. Create a file in the corresponding folder (self.experiment_path_dir)
		"""
		pass

	@abstractmethod
	def generate_plots(self, data: Dict[Any]):
		pass

	def save_data(
		self, data: Dict[Any]
	):  # Data expected to come in format is meant to be JSON-like
		self.store_results(data)
		self.generate_plots(data)
