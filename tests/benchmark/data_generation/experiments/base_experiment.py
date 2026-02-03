from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseExperiment(ABC):
	@abstractmethod
	def run_experiment(self) -> Dict[Any]:
		pass
