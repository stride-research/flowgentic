from abc import ABC, abstractmethod
from typing import Dict, Any


class BasePlotter(ABC):
	def __init__(self) -> None:
		super().__init__()

	@abstractmethod
	def plot_results(self, data: Dict[Any, Any]): ...
