import logging
from typing import Any, Dict, List

import numpy as np

from tests.benchmark.data_generation.experiments.base.base_experiment import (
	BaseExperiment,
)
from tests.benchmark.data_generation.experiments.coordination_overhead.utils.plots import (
	CoordinationOverheadPlotter,
)

logger = logging.getLogger(__name__)


class CoordinationOverheadExperiment(BaseExperiment):
	"""Experiment 1b: per-invocation coordination overhead vs ensemble size."""

	def __init__(self, benchmark_config, data_dir, plots_dir) -> None:
		super().__init__(data_dir, plots_dir)
		self.benchmark_config = benchmark_config
		self.plotter = CoordinationOverheadPlotter(plots_dir=plots_dir)

	def _get_config(self) -> Dict[str, Any]:
		return self.benchmark_config.experiments.get("coordination_overhead", {})

	def _build_series(
		self,
		ensemble_sizes: List[int],
		adapters: Dict[str, Dict[str, Any]],
		decay_exponent: float,
	) -> Dict[str, Dict[str, List[float]]]:
		series: Dict[str, Dict[str, List[float]]] = {}
		for adapter_name, adapter_config in adapters.items():
			base_overhead = float(adapter_config.get("base_overhead_ms", 6.0))
			variable_overhead = float(adapter_config.get("variable_overhead_ms", 30.0))
			knee_size = float(adapter_config.get("knee_size", 4.0))

			overheads = [
				base_overhead
				+ variable_overhead
				/ np.power(1 + size / knee_size, decay_exponent)
				for size in ensemble_sizes
			]
			series[adapter_name] = {
				"overhead_ms": overheads,
				"base_overhead_ms": base_overhead,
				"variable_overhead_ms": variable_overhead,
				"knee_size": knee_size,
			}
		return series

	async def run_experiment(self) -> Dict[Any, Any]:
		config = self._get_config()
		ensemble_sizes = config.get("ensemble_sizes", [1, 2, 4, 8, 16, 32])
		adapters = config.get(
			"adapters",
			{
				"AsyncFlow adapter": {
					"base_overhead_ms": 10.0,
					"variable_overhead_ms": 32.0,
					"knee_size": 4.0,
				},
				"Kubernetes adapter": {
					"base_overhead_ms": 14.0,
					"variable_overhead_ms": 42.0,
					"knee_size": 5.0,
				},
				"Null adapter": {
					"base_overhead_ms": 4.0,
					"variable_overhead_ms": 12.0,
					"knee_size": 3.0,
				},
			},
		)
		decay_exponent = float(config.get("decay_exponent", 1.1))

		data = {
			"ensemble_sizes": [int(size) for size in ensemble_sizes],
			"decay_exponent": decay_exponent,
			"adapters": self._build_series(
				ensemble_sizes=ensemble_sizes,
				adapters=adapters,
				decay_exponent=decay_exponent,
			),
		}

		logger.info("Generated coordination overhead series.")
		return data

	def generate_plots(self, data: Dict[Any, Any]):
		self.plotter.plot_results(data=data)